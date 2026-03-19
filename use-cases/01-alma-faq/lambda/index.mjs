import { BedrockAgentCoreClient, InvokeAgentRuntimeCommand } from "@aws-sdk/client-bedrock-agentcore";
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { DynamoDBDocumentClient, GetCommand, PutCommand, DeleteCommand } from "@aws-sdk/lib-dynamodb";
import { randomUUID } from "crypto";

const REGION = process.env.COMPUTE_REGION || "eu-west-1";
const ALMA_ARN = process.env.ALMA_RUNTIME_ARN || "CHANGE_ME";
const ONBOARDING_ARN = process.env.ONBOARDING_RUNTIME_ARN || "CHANGE_ME";
const TABLE = process.env.SESSION_TABLE || "aibank-session-routing";
const TTL_HOURS = 1;
const LOGIN_URL = process.env.BANKING_LOGIN_URL || "https://banking.aibank.demoaws.com";

const agentcore = new BedrockAgentCoreClient({ region: REGION });
const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({ region: REGION }));

// --- DynamoDB session routing ---
async function getSessionRoute(sessionId) {
  const res = await ddb.send(new GetCommand({ TableName: TABLE, Key: { session_id: sessionId } }));
  return res.Item?.onboarding_session_id || null;
}
async function setOnboarding(sessionId, onboardingSid) {
  await ddb.send(new PutCommand({ TableName: TABLE, Item: {
    session_id: sessionId, onboarding_session_id: onboardingSid,
    ttl: Math.floor(Date.now() / 1000) + TTL_HOURS * 3600,
  }}));
}
async function clearOnboarding(sessionId) {
  await ddb.send(new DeleteCommand({ TableName: TABLE, Key: { session_id: sessionId } }));
}

// --- Call onboarding agent (non-streaming A2A) ---
async function callOnboarding(prompt, onboardingSid) {
  const payload = JSON.stringify({
    jsonrpc: "2.0", id: randomUUID().replace(/-/g, ""),
    method: "message/send",
    params: { message: { role: "user", parts: [{ kind: "text", text: prompt }], messageId: randomUUID().replace(/-/g, "") } }
  });
  const res = await agentcore.send(new InvokeAgentRuntimeCommand({
    agentRuntimeArn: ONBOARDING_ARN, runtimeSessionId: onboardingSid,
    payload, qualifier: "DEFAULT",
  }));
  const raw = await streamToString(res.response || res.body);
  try {
    const parsed = JSON.parse(raw);
    for (const artifact of parsed.result?.artifacts || []) {
      for (const part of artifact.parts || []) {
        if (part.kind === "text") return part.text;
      }
    }
  } catch {}
  return raw;
}

async function streamToString(stream) {
  const chunks = [];
  for await (const chunk of stream) {
    chunks.push(typeof chunk === "string" ? chunk : new TextDecoder().decode(chunk));
  }
  return chunks.join("");
}

function clean(text) {
  return text.replace(/<thinking>[\s\S]*?<\/thinking>/g, "").replace(/<\/?response>/g, "").trim();
}

// --- Main handler: true SSE streaming via Lambda Function URL ---
export const handler = awslambda.streamifyResponse(async (event, responseStream, _context) => {
  // Keep-warm ping from EventBridge
  if (event.source === "aws.events" || event["detail-type"] === "Scheduled Event") {
    responseStream.end();
    return;
  }

  let body;
  try {
    const raw = event.isBase64Encoded
      ? Buffer.from(event.body, "base64").toString()
      : (event.body || "{}");
    body = JSON.parse(raw);
  } catch { body = {}; }

  const prompt = body.message || "Hello";
  let sessionId = body.session_id || randomUUID();
  if (sessionId.length < 33) sessionId = sessionId + "-" + randomUUID().replace(/-/g, "");

  responseStream = awslambda.HttpResponseStream.from(responseStream, {
    statusCode: 200,
    headers: {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "POST, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    },
  });

  try {
    const onboardingSid = await getSessionRoute(sessionId);

    if (onboardingSid) {
      // Route to onboarding agent
      let answer = await callOnboarding(prompt, onboardingSid);
      const onboardingComplete = answer.includes("Account created") || answer.toLowerCase().includes("welcome email");
      if (onboardingComplete) {
        await clearOnboarding(sessionId);
        // Fire async primer to Alma so she knows account was created (P2: non-blocking)
        const primerPrompt = `[SYSTEM] The customer just completed account onboarding successfully. Their account has been created. When they next message, congratulate them warmly, then include this exact line: "You can now log in to your account at [AI Bank Online Banking](${LOGIN_URL})". Then ask if there's anything else you can help with. Do NOT mention the onboarding team or ask them to provide information again.`;
        agentcore.send(new InvokeAgentRuntimeCommand({
          agentRuntimeArn: ALMA_ARN, runtimeSessionId: sessionId,
          payload: JSON.stringify({ prompt: primerPrompt, session_id: sessionId, actor_id: "system" }),
          qualifier: "DEFAULT",
        })).then(r => { const s = r.response || r.body; return (async () => { for await (const _ of s) {} })(); })
          .catch(e => console.error("Async primer failed (non-fatal):", e.message));
      }
      responseStream.write(`data: ${JSON.stringify({ token: clean(answer) })}\n\n`);
      responseStream.write(`data: ${JSON.stringify({ done: true, session_id: sessionId })}\n\n`);
    } else {
      // Stream from Alma agent
      const res = await agentcore.send(new InvokeAgentRuntimeCommand({
        agentRuntimeArn: ALMA_ARN, runtimeSessionId: sessionId,
        payload: JSON.stringify({ prompt, session_id: sessionId, actor_id: "public_user" }),
        qualifier: "DEFAULT",
      }));
      const contentType = res.contentType || "";
      const stream = res.response || res.body;

      let fullText = "";
      let insideThinking = false;
      let thinkingBuffer = "";

      if (contentType.includes("text/event-stream")) {
        let buffer = "";
        for await (const chunk of stream) {
          const text = typeof chunk === "string" ? chunk : new TextDecoder().decode(chunk);
          buffer += text;
          const lines = buffer.split("\n");
          buffer = lines.pop();

          for (const line of lines) {
            if (!line.startsWith("data: ")) continue;
            try {
              const obj = JSON.parse(line.slice(6));
              const token = obj.data || "";
              if (!token) continue;

              // Progressive filter: <thinking> tags + \x00SID:...\x00 + [RELAY_VERBATIM] markers
              // HOLD=50 covers the longest marker (\x00SID:<uuid>\x00 = ~38 chars)
              const HOLD = 50;
              for (let i = 0; i < token.length; i++) {
                const ch = token[i];
                if (insideThinking) {
                  thinkingBuffer += ch;
                  if (thinkingBuffer.endsWith("</thinking>")) { insideThinking = false; thinkingBuffer = ""; }
                } else {
                  thinkingBuffer += ch;
                  if (thinkingBuffer.endsWith("<thinking>")) {
                    insideThinking = true;
                  } else if (thinkingBuffer.length >= HOLD) {
                    const safe = thinkingBuffer.slice(0, thinkingBuffer.length - (HOLD - 1));
                    thinkingBuffer = thinkingBuffer.slice(-(HOLD - 1));
                    if (safe) {
                      const cleaned = safe.replace(/<\/?response>/g, "").replace(/\x00SID:[a-f0-9-]+\x00/g, "").replace(/\[RELAY_VERBATIM\]/g, "");
                      if (cleaned) { fullText += cleaned; responseStream.write(`data: ${JSON.stringify({ token: cleaned })}\n\n`); }
                    }
                  }
                }
              }
            } catch {}
          }
        }
        // Flush remaining buffer
        if (!insideThinking && thinkingBuffer) {
          const cleaned = thinkingBuffer.replace(/<\/?response>/g, "").replace(/\x00SID:[a-f0-9-]+\x00/g, "").replace(/\[RELAY_VERBATIM\]/g, "");
          if (cleaned) { fullText += cleaned; responseStream.write(`data: ${JSON.stringify({ token: cleaned })}\n\n`); }
        }
      } else {
        const raw = await streamToString(stream);
        const answer = clean(JSON.parse(raw).answer || raw);
        fullText = answer;
        responseStream.write(`data: ${JSON.stringify({ token: answer })}\n\n`);
      }

      // P3: Extract \x00SID:<uuid>\x00 from fullText to set onboarding session routing
      const sidMatch = fullText.match(/\x00SID:([a-f0-9-]+)\x00/);
      if (sidMatch) {
        await setOnboarding(sessionId, sidMatch[1]);
      } else {
        // Fallback: signal-based detection
        const lower = fullText.toLowerCase();
        const signals = ["onboarding team","connect you with","account opening","first name","date of birth","email address","phone number","nationality","guide you through","registration","open your"];
        if (signals.filter(s => lower.includes(s)).length >= 2) {
          const newSid = randomUUID();
          await callOnboarding(prompt, newSid);
          await setOnboarding(sessionId, newSid);
        }
      }

      responseStream.write(`data: ${JSON.stringify({ done: true, session_id: sessionId })}\n\n`);
    }
  } catch (err) {
    console.error("ERROR:", err);
    responseStream.write(`data: ${JSON.stringify({ token: "I'm sorry, something went wrong. Please try again." })}\n\n`);
    responseStream.write(`data: ${JSON.stringify({ done: true, session_id: sessionId })}\n\n`);
  }

  responseStream.end();
});

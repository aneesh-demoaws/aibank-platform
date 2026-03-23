import json, uuid, time, os, boto3, base64

ddb = boto3.resource("dynamodb", region_name=os.environ.get("SESSION_REGION", "eu-west-1"))
table = ddb.Table(os.environ.get("SESSION_TABLE", "aibank-session-routing"))
TTL_S = 86400

COGNITO_REGION = os.environ.get("COGNITO_REGION", "me-south-1")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "5h3dqbpddps11fjvan9k2jdd88")

# Employee pool (eu-west-1)
EMP_COGNITO_REGION    = os.environ.get("EMP_COGNITO_REGION", "eu-west-1")
EMP_COGNITO_CLIENT_ID = os.environ.get("EMP_COGNITO_CLIENT_ID", "2kf9i0tnjgvkchuh411nk3phji")
CLUSTER_ARN = "arn:aws:rds:me-south-1:519124228967:cluster:aibank-core-banking"
SECRET_ARN = "arn:aws:secretsmanager:me-south-1:519124228967:secret:aibank-core-banking-credentials-DEdCPJ"

cognito = boto3.client("cognito-idp", region_name=COGNITO_REGION)
rds = boto3.client("rds-data", region_name="me-south-1")


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    if method == "OPTIONS":
        return _resp(200, "")
    if method == "POST" and path == "/sessions/login":
        return _login(event)
    if method == "GET" and path == "/sessions/me":
        return _get_session(event)
    if method == "DELETE" and path == "/sessions/me":
        return _logout(event)
    if method == "GET" and path == "/sessions/me/accounts":
        return _get_accounts(event)
    if method == "GET" and path == "/sessions/me/transactions":
        return _get_transactions(event)
    if method == "GET" and path == "/sessions/me/loans":
        return _get_loans(event)
    if method == "GET" and path == "/sessions/me/offers":
        return _get_offers(event)

    return _resp(404, {"error": "not found"})


def _login(event):
    body = json.loads(event.get("body") or "{}")
    email    = body.get("email", "").strip().lower()
    password = body.get("password", "")
    portal   = body.get("portal", "customer")

    if not email or not password:
        return _resp(400, {"error": "Email and password required"})

    # Select correct Cognito pool based on portal
    if portal == "employee":
        cog = boto3.client("cognito-idp", region_name=EMP_COGNITO_REGION)
        client_id = EMP_COGNITO_CLIENT_ID
    else:
        cog = cognito
        client_id = COGNITO_CLIENT_ID

    try:
        auth_resp = cog.initiate_auth(
            ClientId=client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": email, "PASSWORD": password},
        )
    except cog.exceptions.NotAuthorizedException:
        return _resp(401, {"error": "Invalid email or password"})
    except cog.exceptions.UserNotFoundException:
        return _resp(401, {"error": "Invalid email or password"})
    except Exception as e:
        return _resp(401, {"error": str(e)})

    if auth_resp.get("ChallengeName") == "NEW_PASSWORD_REQUIRED":
        return _resp(200, {"challenge": "NEW_PASSWORD_REQUIRED", "session": auth_resp["Session"]})

    tokens    = auth_resp["AuthenticationResult"]
    user_info = cog.get_user(AccessToken=tokens["AccessToken"])
    attrs     = {a["Name"]: a["Value"] for a in user_info["UserAttributes"]}

    given  = attrs.get("given_name", "")
    family = attrs.get("family_name", "")
    name   = f"{given} {family}".strip() or attrs.get("name", "") or email.split("@")[0]
    sub    = attrs.get("sub", "")
    # employees don't have custom:customer_id — use sub as identity
    customer_id = attrs.get("custom:customer_id", "") if portal == "customer" else ""
    if portal == "employee":
        groups = cog.admin_list_groups_for_user(
            UserPoolId=EMP_COGNITO_REGION + "_ALFkxDepn",
            Username=email
        ).get("Groups", [])
        group_name = groups[0]["GroupName"] if groups else "employee"
        # Normalize group names to UI role keys (loan-officers -> loan-officer)
        ROLE_MAP = {"loan-officers": "loan-officer", "risk-analysts": "risk-analyst",
                    "branch-managers": "branch-manager", "admins": "admin",
                    "relationship-managers": "rm"}
        role = ROLE_MAP.get(group_name, group_name.rstrip("s"))
    else:
        role = portal

    return _create_session(email, name, sub, customer_id, portal, "BH", role=role)


def _create_session(email, name, sub, customer_id, portal, country, role=None):
    sid = str(uuid.uuid4())
    now = int(time.time() * 1000)
    exp = int(time.time()) + TTL_S

    table.put_item(Item={
        "session_id":   sid,
        "user_email":   email,
        "user_name":    name,
        "customer_id":  customer_id,
        "country":      country,
        "portal":       portal,
        "role":         role or portal,
        "status":       "active",
        "cognito_pool": f"{COGNITO_REGION}_mA8ojX4Yv",
        "cognito_sub":  sub,
        "created_at":   now,
        "last_active":  now,
        "idle_timeout": TTL_S * 1000,
        "expires_at":   exp,
    })

    cookie = f"aibank_sid={sid}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={TTL_S}"
    return _resp(200, {"ok": True, "name": name, "email": email, "portal": portal}, cookies=[cookie])


def _get_session(event):
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    return _resp(200, {
        "name": item.get("user_name"),
        "email": item.get("user_email"),
        "portal": item.get("portal"),
        "role": item.get("role"),
        "country": item.get("country"),
        "customer_id": item.get("customer_id", ""),
    })


def _get_accounts(event):
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})

    # Always resolve customer_id from Aurora via cognito_sub — never trust session-stored value
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    # Accounts with monthly spend/income computed from transactions
    acct_rows = _sql(
        """SELECT a.account_id, a.account_type, a.balance, a.currency, a.account_number, a.status,
            COALESCE(SUM(CASE WHEN t.transaction_type='debit'
                AND YEAR(t.transaction_date)=YEAR(NOW()) AND MONTH(t.transaction_date)=MONTH(NOW())
                THEN t.amount ELSE 0 END), 0) AS monthly_spend,
            COALESCE(SUM(CASE WHEN t.transaction_type='credit'
                AND YEAR(t.transaction_date)=YEAR(NOW()) AND MONTH(t.transaction_date)=MONTH(NOW())
                THEN t.amount ELSE 0 END), 0) AS monthly_income
           FROM accounts a LEFT JOIN transactions t ON t.account_id = a.account_id
           WHERE a.customer_id = :cid AND a.status = 'ACTIVE'
           GROUP BY a.account_id""",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )

    accounts = []
    for r in acct_rows:
        spend = float(r[6].get("stringValue") or r[6].get("doubleValue") or 0)
        income = float(r[7].get("stringValue") or r[7].get("doubleValue") or 0)
        savings_rate = round((income - spend) / income * 100) if income > 0 else 0
        accounts.append({
            "account_id": r[0].get("stringValue"),
            "account_type": r[1].get("stringValue", "").capitalize(),
            "balance": float(r[2].get("stringValue") or r[2].get("doubleValue") or 0),
            "currency": r[3].get("stringValue", "BHD"),
            "account_number": r[4].get("stringValue", ""),
            "monthly_spend": spend,
            "monthly_income": income,
            "savings_rate": max(0, savings_rate),
        })

    # Active loans
    loan_rows = _sql(
        "SELECT application_id, loan_type, amount, status, monthly_payment FROM loan_applications WHERE customer_id = :cid AND status = 'approved'",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    loans = [{"loan_id": r[0].get("stringValue"), "loan_type": r[1].get("stringValue"),
               "amount": float(r[2].get("stringValue") or r[2].get("doubleValue") or 0),
               "status": r[3].get("stringValue"),
               "monthly_payment": float(r[4].get("stringValue") or r[4].get("doubleValue") or 0) if r[4] else 0}
             for r in loan_rows]

    # KYC status from customers table
    kyc_status = "PENDING"
    try:
        kyc_rows = _sql(
            "SELECT kyc_status FROM customers WHERE customer_id = :cid LIMIT 1",
            [{"name": "cid", "value": {"stringValue": customer_id}}]
        )
        if kyc_rows:
            kyc_status = kyc_rows[0][0].get("stringValue", "PENDING")
    except Exception:
        pass

    return _resp(200, {"accounts": accounts, "loans": loans, "kyc_status": kyc_status})


def _get_transactions(event):
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})

    # Always resolve customer_id from Aurora via cognito_sub — never trust session-stored value
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    qs = event.get("queryStringParameters") or {}
    limit = min(int(qs.get("limit", 10)), 50)

    rows = _sql(
        """SELECT t.transaction_id, t.account_id, t.transaction_type, t.amount, t.currency,
            t.description, t.merchant_name, t.transaction_date, t.balance_after, t.status
           FROM transactions t
           JOIN accounts a ON a.account_id = t.account_id
           WHERE a.customer_id = :cid AND t.status = 'completed'
           ORDER BY t.transaction_date DESC
           LIMIT :lim""",
        [{"name": "cid", "value": {"stringValue": customer_id}},
         {"name": "lim", "value": {"longValue": limit}}]
    )

    txns = []
    for r in rows:
        txn_type = r[2].get("stringValue", "debit")
        amount = float(r[3].get("stringValue") or r[3].get("doubleValue") or 0)
        txns.append({
            "transaction_id": r[0].get("stringValue"),
            "account_id": r[1].get("stringValue"),
            "type": txn_type,
            "amount": amount if txn_type == "credit" else -amount,
            "currency": r[4].get("stringValue", "BHD"),
            "description": r[5].get("stringValue") or r[6].get("stringValue") or "Transaction",
            "date": str(r[7].get("stringValue", ""))[:10],
            "balance_after": float(r[8].get("stringValue") or r[8].get("doubleValue") or 0),
        })

    return _resp(200, {"transactions": txns})


def _get_loans(event):
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    rows = _sql(
        "SELECT application_id, loan_type, amount, status, monthly_payment, duration, interest, created_at FROM loan_applications WHERE customer_id = :cid ORDER BY created_at DESC",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    loans = [{"loan_id": r[0].get("stringValue"), "loan_type": r[1].get("stringValue"),
               "amount": float(r[2].get("stringValue") or r[2].get("doubleValue") or 0),
               "status": r[3].get("stringValue"),
               "monthly_payment": float(r[4].get("stringValue") or r[4].get("doubleValue") or 0) if not r[4].get("isNull") else 0,
               "duration": int(r[5].get("longValue") or 0) if not r[5].get("isNull") else 0,
               "interest": float(r[6].get("stringValue") or r[6].get("doubleValue") or 0) if not r[6].get("isNull") else 0,
               "created_at": str(r[7].get("stringValue", ""))[:10]}
              for r in rows]
    return _resp(200, {"loans": loans})


def _get_offers(event):
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    rows = _sql(
        "SELECT offer_id, product_type, offer_title, offer_subtitle, offer_description, confidence_score, offer_amount, interest_rate, tenure_months, monthly_payment, call_to_action, valid_until FROM next_best_offers WHERE customer_id = :cid AND status = 'active' ORDER BY priority_rank ASC",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    offers = [{"offer_id": r[0].get("stringValue"), "product_type": r[1].get("stringValue"),
                "title": r[2].get("stringValue"), "subtitle": r[3].get("stringValue"),
                "description": r[4].get("stringValue"),
                "confidence": float(r[5].get("stringValue") or r[5].get("doubleValue") or 0),
                "amount": float(r[6].get("stringValue") or r[6].get("doubleValue") or 0) if not r[6].get("isNull") else None,
                "interest_rate": float(r[7].get("stringValue") or r[7].get("doubleValue") or 0) if not r[7].get("isNull") else None,
                "tenure_months": int(r[8].get("longValue") or 0) if not r[8].get("isNull") else None,
                "monthly_payment": float(r[9].get("stringValue") or r[9].get("doubleValue") or 0) if not r[9].get("isNull") else None,
                "cta": r[10].get("stringValue"), "valid_until": str(r[11].get("stringValue", ""))[:10]}
               for r in rows]
    return _resp(200, {"offers": offers})
    sid = _extract_sid(event)
    if sid:
        try:
            table.update_item(
                Key={"session_id": sid},
                UpdateExpression="SET #s = :v",
                ExpressionAttributeNames={"#s": "status"},
                ExpressionAttributeValues={":v": "ended"},
            )
        except Exception:
            pass
    cookie = "aibank_sid=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"
    return _resp(200, {"ok": True}, cookies=[cookie])


def _resolve_customer_id_by_sub(cognito_sub):
    """Resolve customer_id from Aurora using Cognito sub — the only tamper-proof identity anchor."""
    if not cognito_sub:
        return None
    rows = _sql(
        "SELECT customer_id FROM customers WHERE cognito_user_id = :sub AND status = 'ACTIVE' LIMIT 1",
        [{"name": "sub", "value": {"stringValue": cognito_sub}}]
    )
    return rows[0][0].get("stringValue") if rows else None


def _sql(sql, params):
    resp = rds.execute_statement(
        resourceArn=CLUSTER_ARN, secretArn=SECRET_ARN,
        database="corebanking", sql=sql, parameters=params
    )
    return resp.get("records", [])


def _load_session(sid):
    resp = table.get_item(Key={"session_id": sid})
    item = resp.get("Item")
    if not item or item.get("status") != "active":
        return None
    return item


def _extract_sid(event):
    for c in event.get("cookies", []):
        if c.startswith("aibank_sid="):
            return c.split("=", 1)[1]
    raw = event.get("headers", {}).get("cookie", "")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == "aibank_sid":
            return v
    return None


def _resp(status, body, cookies=None):
    resp = {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body) if isinstance(body, dict) else body,
    }
    if cookies:
        resp["cookies"] = cookies
    return resp

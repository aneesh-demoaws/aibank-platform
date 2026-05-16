import json, uuid, time, os, boto3, base64

ddb = boto3.resource("dynamodb", region_name=os.environ.get("SESSION_REGION", "eu-west-1"))
table = ddb.Table(os.environ.get("SESSION_TABLE", "aibank-session-routing"))
TTL_S = 86400

COGNITO_REGION = os.environ.get("COGNITO_REGION", "eu-west-1")
COGNITO_CLIENT_ID = os.environ.get("COGNITO_CLIENT_ID", "3ttgkg5u7o1djd9j5tdjs424og")

# Employee pool (eu-west-1)
EMP_COGNITO_REGION    = os.environ.get("EMP_COGNITO_REGION", "eu-west-1")
EMP_COGNITO_CLIENT_ID = os.environ.get("EMP_COGNITO_CLIENT_ID", "2kf9i0tnjgvkchuh411nk3phji")
CLUSTER_ARN = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET_ARN = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"

cognito = boto3.client("cognito-idp", region_name=COGNITO_REGION)
rds = boto3.client("rds-data", region_name=os.environ.get("SESSION_REGION", "eu-west-1"))


def handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")

    if method == "OPTIONS":
        return _resp(200, "")
    if method == "POST" and path == "/sessions/login":
        return _login(event)
    if method == "POST" and path == "/sessions/demo":
        return _demo_login(event)
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
    if method == "GET" and path == "/sessions/me/actions":
        return _get_actions(event)
    if method == "POST" and path.startswith("/sessions/me/actions/") and path.endswith("/event"):
        return _post_action_event(event, path)
    if method == "GET" and path.startswith("/sessions/me/actions/") and path.endswith("/explain"):
        return _get_action_explain(event, path)

    # --- NBA preferences (V015: bidirectional category/template suppressions) ---
    if method == "GET" and path == "/sessions/me/nba-preferences":
        return _list_nba_preferences(event)
    if method == "POST" and path == "/sessions/me/nba-preferences":
        return _create_nba_preference(event)
    if method == "DELETE" and path.startswith("/sessions/me/nba-preferences/"):
        return _delete_nba_preference(event, path)

    if method == "GET" and path == "/sessions/me/financial-health":
        return _get_financial_health(event)
    if method == "POST" and path == "/sessions/sso":
        return _sso_login(event)
    if method == "POST" and path == "/sessions/cognito":
        return _sso_login(event)
    if method == "POST" and path == "/sessions/me/reset-nba":
        return _reset_nba(event)
    if method == "POST" and path == "/sessions/me/transfer":
        return _execute_transfer(event)

    return _resp(404, {"error": "not found"})




ROLE_CONFIG_TABLE = "aibank-role-config"
_role_cache = {}

def _get_role_from_config(group_name):
    """Lookup role from DynamoDB aibank-role-config table. Cached per Lambda instance."""
    group_key = group_name.lower().strip()
    if group_key in _role_cache:
        return _role_cache[group_key]
    try:
        resp = ddb.Table(ROLE_CONFIG_TABLE).get_item(Key={"ad_group": group_key})
        item = resp.get("Item", {})
        role = item.get("role", group_key.rstrip("s"))
        _role_cache[group_key] = role
        return role
    except Exception:
        return group_key.rstrip("s")

def _demo_login(event):
    """Create a demo session without Cognito auth.

    Used by the landing-page "Try it" buttons (Customer / RM / Platform Admin).
    Marked with `demo: True` in DDB for audit; otherwise identical to a real
    Cognito session (same table, same TTL, same cookie).

    Request body: {"portal": "customer|employee", "role": "admin|rm|customer|...",
                   "country": "BH|..."}
    """
    body = json.loads(event.get("body") or "{}")
    portal = (body.get("portal") or "customer").strip().lower()
    role = (body.get("role") or portal).strip().lower()
    country = (body.get("country") or "BH").strip().upper()

    if portal not in ("customer", "employee"):
        return _resp(400, {"error": "portal must be 'customer' or 'employee'"})

    demo_profiles = {
        # Customer demo user — bind to a real Aurora customer so dashboards work
        ("customer", "customer"): {
            "email": "customer@aibank.demo",
            "name": "Demo Customer",
            "sub":  "32456404-6091-703d-75e7-115c15fafb27",
            "customer_id": "CUST20250100",
        },
        ("employee", "admin"):          {"email": "admin@aibank.demo",     "name": "IT Administrator",  "sub": "demo-admin-0001"},
        ("employee", "rm"):             {"email": "rm@aibank.demo",        "name": "Fatima Al-Khalifa", "sub": "demo-rm-0001"},
        ("employee", "loan-officer"):   {"email": "officer@aibank.demo",   "name": "Khalid Al-Rashid",  "sub": "demo-officer-0001"},
        ("employee", "risk-analyst"):   {"email": "risk@aibank.demo",      "name": "Noura Al-Mansouri", "sub": "demo-risk-0001"},
        ("employee", "operations"):     {"email": "ops@aibank.demo",       "name": "Ahmed Al-Zayani",   "sub": "demo-ops-0001"},
        ("employee", "marketing"):      {"email": "marketing@aibank.demo", "name": "Layla Al-Sabah",    "sub": "demo-marketing-0001"},
        ("employee", "branch-manager"): {"email": "branch@aibank.demo",    "name": "Yousif Al-Khalifa", "sub": "demo-branch-0001"},
    }
    profile = demo_profiles.get((portal, role))
    if profile is None:
        if portal == "employee":
            profile = {
                "email": f"{role or 'employee'}@aibank.demo",
                "name": (role or "employee").replace("-", " ").title(),
                "sub":  f"demo-{role or 'employee'}-0001",
            }
        else:
            profile = {
                "email": "customer@aibank.demo",
                "name": "Demo Customer",
                "sub":  "32456404-6091-703d-75e7-115c15fafb27",
                "customer_id": "CUST20250100",
            }

    return _create_session(
        email=profile["email"],
        name=profile["name"],
        sub=profile["sub"],
        customer_id=profile.get("customer_id", ""),
        portal=portal,
        country=country,
        role=role,
        demo=True,
    )


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
        # Role determination from DynamoDB config table (aibank-role-config)
        # Reads AD group → role mapping dynamically (no hardcoded map)
        group_from_saml = attrs.get("custom:groups", "")
        if group_from_saml:
            import urllib.parse as _up
            decoded = _up.unquote(group_from_saml).strip("[] ")
            group_name = "employee"
            for g in decoded.split(","):
                g = g.strip()
                if "//" in g:
                    sid = g.split("//")[1].lower()
                    test_role = _get_role_from_config(sid)
                    if test_role != sid:
                        group_name = sid
                        break
                elif "@" in g:
                    group_name = g.split("@")[0].lower()
                    break
        else:
            # Fallback: read from Cognito groups (native users)
            try:
                cog_groups = cog.admin_list_groups_for_user(
                    UserPoolId="eu-west-1_ALFkxDepn",
                    Username=user_info["Username"]
                ).get("Groups", [])
                group_name = cog_groups[0]["GroupName"] if cog_groups else "employee"
            except Exception:
                group_name = "employee"

        # Lookup role from DynamoDB
        role = _get_role_from_config(group_name)
    else:
        role = portal

    return _create_session(email, name, sub, customer_id, portal, "BH", role=role)


def _create_session(email, name, sub, customer_id, portal, country, role=None, demo=False):
    sid = str(uuid.uuid4())
    now = int(time.time() * 1000)
    exp = int(time.time()) + TTL_S

    item = {
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
    }
    if demo:
        item["demo"] = True
    table.put_item(Item=item)

    cookie = f"aibank_sid={sid}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age={TTL_S}"
    return _resp(200, {"ok": True, "name": name, "email": email, "portal": portal, "role": role or portal}, cookies=[cookie])


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

    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    qs = event.get("queryStringParameters") or {}
    limit = min(int(qs.get("limit", 10) or 10), 500)
    from_date = qs.get("from_date")  # ISO YYYY-MM-DD
    to_date = qs.get("to_date")
    account_id_filter = qs.get("account_id")
    txn_type_filter = qs.get("type")  # credit | debit
    search = (qs.get("search") or "").strip()

    sql_parts = ["a.customer_id = :cid", "t.status = 'completed'"]
    params = [{"name": "cid", "value": {"stringValue": customer_id}}]

    if from_date:
        sql_parts.append("DATE(t.transaction_date) >= :from_d")
        params.append({"name": "from_d", "value": {"stringValue": from_date}})
    if to_date:
        sql_parts.append("DATE(t.transaction_date) <= :to_d")
        params.append({"name": "to_d", "value": {"stringValue": to_date}})
    if account_id_filter:
        sql_parts.append("t.account_id = :aid")
        params.append({"name": "aid", "value": {"stringValue": account_id_filter}})
    if txn_type_filter in ("credit", "debit"):
        sql_parts.append("t.transaction_type = :tt")
        params.append({"name": "tt", "value": {"stringValue": txn_type_filter}})
    if search:
        sql_parts.append("(LOWER(t.description) LIKE :q OR LOWER(t.merchant_name) LIKE :q OR LOWER(t.transaction_id) LIKE :q)")
        params.append({"name": "q", "value": {"stringValue": f"%{search.lower()}%"}})

    where_clause = " AND ".join(sql_parts)

    rows = _sql(
        f"""SELECT t.transaction_id, t.account_id, t.transaction_type, t.amount, t.currency,
            t.description, t.merchant_name, t.transaction_date, t.balance_after, t.status,
            t.category_id, t.mcc_code
           FROM transactions t
           JOIN accounts a ON a.account_id = t.account_id
           WHERE {where_clause}
           ORDER BY t.transaction_date DESC
           LIMIT :lim""",
        params + [{"name": "lim", "value": {"longValue": limit}}]
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
        "SELECT application_id, loan_type, amount, status, monthly_payment, duration, interest, created_at, "
        "purpose, decision_type, disbursement_status, disbursement_txn_id, disbursed_at "
        "FROM loan_applications WHERE customer_id = :cid ORDER BY created_at DESC",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    def _strv(c):
        if not c or c.get("isNull"): return None
        return c.get("stringValue") or c.get("doubleValue") or c.get("longValue") or ""
    def _floatv(c):
        if not c or c.get("isNull"): return 0
        return float(c.get("stringValue") or c.get("doubleValue") or 0)
    def _intv(c):
        if not c or c.get("isNull"): return 0
        return int(c.get("longValue") or c.get("stringValue") or 0)

    loans = []
    for r in rows:
        loans.append({
            "application_id": _strv(r[0]),
            "loan_id": _strv(r[0]),  # back-compat
            "loan_type": _strv(r[1]),
            "amount": _floatv(r[2]),
            "status": _strv(r[3]),
            "monthly_payment": _floatv(r[4]),
            "duration": _intv(r[5]),
            "interest": _floatv(r[6]),
            "created_at": str(_strv(r[7]) or "")[:19],
            "purpose": _strv(r[8]),
            "decision_type": _strv(r[9]),
            "disbursement_status": _strv(r[10]),
            "disbursement_txn_id": _strv(r[11]),
            "disbursed_at": str(_strv(r[12]) or "")[:19],
        })
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


def _logout(event):
    """DELETE /sessions/me — destroy the session and clear the cookie."""
    sid = _extract_sid(event)
    if sid:
        try:
            ddb.Table(SESSIONS_TABLE).delete_item(Key={"sid": sid})
        except Exception:
            pass
    # Clear cookie
    return _resp(200, {"ok": True}, cookies=["aibank_sid=; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=0"])



def _execute_transfer(event):
    """Execute a fund transfer between accounts.

    Body: {from_account, to_account, beneficiary_customer_id, amount, description, transfer_type}
    transfer_type: 'own' (between customer's accounts) | 'other' (to another customer)
    """
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})

    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _resp(400, {"error": "Invalid JSON"})

    from_account = body.get("from_account", "")
    to_account = body.get("to_account") or None
    beneficiary_id = body.get("beneficiary_customer_id") or None
    amount = float(body.get("amount", 0) or 0)
    description = body.get("description", "Transfer")[:100]
    transfer_type = body.get("transfer_type", "own")

    if not from_account or amount <= 0:
        return _resp(400, {"error": "from_account and positive amount are required"})

    # Verify the source account belongs to this customer
    own_accounts = _sql(
        "SELECT account_id, balance FROM accounts WHERE customer_id = :cid AND UPPER(status) = 'ACTIVE'",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    own_ids = {_v(r[0]): float(_v(r[1]) or 0) for r in own_accounts}
    if from_account not in own_ids:
        return _resp(403, {"error": "Source account does not belong to you"})
    if own_ids[from_account] < amount:
        return _resp(400, {"error": f"Insufficient funds. Available: BHD {own_ids[from_account]:.3f}"})

    # Determine target
    if transfer_type == "own":
        if not to_account or to_account not in own_ids:
            return _resp(400, {"error": "to_account must be one of your accounts for internal transfer"})
        target_account = to_account
        target_label = f"{customer_id} ({to_account})"
    else:
        if not beneficiary_id:
            return _resp(400, {"error": "beneficiary_customer_id required for external transfer"})
        # Find beneficiary's primary account
        target_lookup = _sql(
            "SELECT account_id FROM accounts WHERE customer_id = :bid AND UPPER(status) = 'ACTIVE' ORDER BY account_id LIMIT 1",
            [{"name": "bid", "value": {"stringValue": beneficiary_id}}]
        )
        target_records = target_lookup
        if not target_records:
            return _resp(404, {"error": f"Beneficiary {beneficiary_id} has no active accounts"})
        target_account = _v(target_records[0][0])
        target_label = f"{beneficiary_id} ({target_account})"

    # Execute the transfer atomically: debit source, credit target, two transaction rows
    import uuid
    txn_debit = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    txn_credit = f"TXN-{uuid.uuid4().hex[:12].upper()}"
    new_balance_from = own_ids[from_account] - amount

    try:
        # Debit source
        _sql(
            "INSERT INTO transactions (transaction_id, account_id, transaction_type, amount, "
            "description, merchant_name, transaction_date, balance_after, value_date, status) "
            "VALUES (:tid, :aid, 'debit', :amt, :desc, :merch, NOW(), :bal, CURDATE(), 'completed')",
            [
                {"name": "tid", "value": {"stringValue": txn_debit}},
                {"name": "aid", "value": {"stringValue": from_account}},
                {"name": "amt", "value": {"doubleValue": amount}},
                {"name": "desc", "value": {"stringValue": f"Transfer to {target_label}: {description}"[:255]}},
                {"name": "merch", "value": {"stringValue": "AI Bank Transfer"}},
                {"name": "bal", "value": {"doubleValue": new_balance_from}},
            ]
        )
        _sql(
            "UPDATE accounts SET balance = balance - :amt WHERE account_id = :aid",
            [{"name": "amt", "value": {"doubleValue": amount}},
             {"name": "aid", "value": {"stringValue": from_account}}]
        )

        # Credit target
        target_bal = _sql(
            "SELECT balance FROM accounts WHERE account_id = :aid",
            [{"name": "aid", "value": {"stringValue": target_account}}]
        )
        target_balance = float(_v(target_bal[0][0]) or 0) if target_bal else 0
        new_balance_to = target_balance + amount

        _sql(
            "INSERT INTO transactions (transaction_id, account_id, transaction_type, amount, "
            "description, merchant_name, transaction_date, balance_after, value_date, status) "
            "VALUES (:tid, :aid, 'credit', :amt, :desc, :merch, NOW(), :bal, CURDATE(), 'completed')",
            [
                {"name": "tid", "value": {"stringValue": txn_credit}},
                {"name": "aid", "value": {"stringValue": target_account}},
                {"name": "amt", "value": {"doubleValue": amount}},
                {"name": "desc", "value": {"stringValue": f"Transfer from {customer_id} ({from_account}): {description}"[:255]}},
                {"name": "merch", "value": {"stringValue": "AI Bank Transfer"}},
                {"name": "bal", "value": {"doubleValue": new_balance_to}},
            ]
        )
        _sql(
            "UPDATE accounts SET balance = balance + :amt WHERE account_id = :aid",
            [{"name": "amt", "value": {"doubleValue": amount}},
             {"name": "aid", "value": {"stringValue": target_account}}]
        )

        return _resp(200, {
            "success": True,
            "debit_transaction_id": txn_debit,
            "credit_transaction_id": txn_credit,
            "from_account": from_account,
            "to_account": target_account,
            "amount": amount,
            "new_balance": round(new_balance_from, 3),
        })
    except Exception as e:
        logger.exception(f"Transfer failed: {e}")
        return _resp(500, {"error": f"Transfer failed: {str(e)}"})


def _v(field):
    """Helper to extract value from rds-data field, handling NULL."""
    if not field or field.get("isNull"):
        return None
    return list(field.values())[0]

def _resp(status, body, cookies=None):
    resp = {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body) if isinstance(body, dict) else body,
    }
    if cookies:
        resp["cookies"] = cookies
    return resp


def _get_actions(event):
    """GET /sessions/me/actions — return top-8 active NBAs for the customer."""
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    qs = event.get("queryStringParameters") or {}
    category_filter = qs.get("category")
    limit = min(int(qs.get("limit", 8)), 20)

    sql = ("SELECT action_id, template_id, category, priority, confidence, title, "
           "reasoning, metrics, cta_primary, cta_secondary, source, product_type, "
           "generated_at, view_count "
           "FROM next_best_actions "
           "WHERE customer_id = :cid AND status = 'active' "
           "AND (expires_at IS NULL OR expires_at > NOW())")
    params = [{"name": "cid", "value": {"stringValue": customer_id}}]

    if category_filter:
        sql += " AND category = :cat"
        params.append({"name": "cat", "value": {"stringValue": category_filter}})

    sql += " ORDER BY priority DESC, generated_at DESC LIMIT :lim"
    params.append({"name": "lim", "value": {"longValue": limit}})

    rows = _sql(sql, params)
    actions = []
    for r in rows:
        def val(i):
            v = r[i]
            if v.get("isNull"):
                return None
            return v.get("stringValue") or v.get("longValue") or v.get("doubleValue")

        metrics = val(7)
        cta_primary = val(8)
        cta_secondary = val(9)

        # Parse JSON fields
        import json as _json
        try:
            metrics = _json.loads(metrics) if metrics else None
        except:
            metrics = None
        try:
            cta_primary = _json.loads(cta_primary) if cta_primary else None
        except:
            cta_primary = None
        try:
            cta_secondary = _json.loads(cta_secondary) if cta_secondary else None
        except:
            cta_secondary = None

        actions.append({
            "action_id": val(0),
            "template_id": val(1),
            "category": val(2),
            "priority": val(3),
            "confidence": float(val(4)) if val(4) else 0.8,
            "title": val(5),
            "reasoning": val(6),
            "metrics": metrics,
            "cta_primary": cta_primary,
            "cta_secondary": cta_secondary,
            "source": val(10),
            "product_type": val(11),
            "generated_at": str(val(12) or "")[:19],
            "view_count": val(13) or 0,
        })

    # Check which product_types the customer already owns (actioned)
    owned_rows = _sql(
        "SELECT product_type FROM customer_products WHERE customer_id=:cid AND status='active'",
        [{"name": "cid", "value": {"stringValue": customer_id}}])
    owned_types = {list(r[0].values())[0] for r in owned_rows if not r[0].get("isNull")}

    # Template → product_type mapping (fallback when product_type column is NULL)
    _tpl_product = {
        'opportunity.travel_insurance_on_trip': 'travel_insurance_international',
        'opportunity.fixed_deposit': 'fixed_deposit',
        'opportunity.goal_saver_for_child': 'goal_saver',
        'wellness.salary_day_allocation': 'salary_allocation',
    }

    # Mark actioned NBAs
    for a in actions:
        pt = a.get("product_type") or _tpl_product.get(a.get("template_id", ""), "")
        a["actioned"] = pt in owned_types if pt else False

    # Category cap: max 2 per category, max 8 total (BRD §5.3)
    capped = []
    cat_counts = {}
    for a in actions:
        cat = a.get("category", "other")
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
        if cat_counts[cat] <= 3 and len(capped) < 10:
            capped.append(a)

    return _resp(200, {"actions": capped, "count": len(capped), "customer_id": customer_id})


def _post_action_event(event, path):
    """POST /sessions/me/actions/{action_id}/event — log view/click/dismiss/convert."""
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    # Extract action_id from path: /sessions/me/actions/{action_id}/event
    parts = path.strip("/").split("/")
    if len(parts) < 5:
        return _resp(400, {"error": "Invalid path"})
    action_id = parts[3]

    import json as _json
    body = _json.loads(event.get("body") or "{}")
    evt = body.get("event", "").lower()
    if evt not in ("viewed", "clicked", "dismissed", "converted"):
        return _resp(400, {"error": "event must be viewed|clicked|dismissed|converted"})

    channel = body.get("channel", "in_app")
    dismiss_reason = body.get("dismiss_reason")
    conversion_value = body.get("conversion_value")
    conversion_ref = body.get("conversion_ref")
    metadata = _json.dumps(body.get("metadata")) if body.get("metadata") else None

    # Get template_id from the action
    action_rows = _sql(
        "SELECT template_id FROM next_best_actions WHERE action_id = :aid AND customer_id = :cid",
        [{"name": "aid", "value": {"stringValue": action_id}},
         {"name": "cid", "value": {"stringValue": customer_id}}]
    )
    if not action_rows:
        return _resp(404, {"error": "Action not found"})
    template_id = list(action_rows[0][0].values())[0]

    # Insert interaction
    sql = ("INSERT INTO nba_interactions (action_id, customer_id, template_id, event, channel"
           + (", dismiss_reason" if dismiss_reason else "")
           + (", conversion_value" if conversion_value else "")
           + (", conversion_ref" if conversion_ref else "")
           + (", metadata" if metadata else "")
           + (", session_id" if sid else "")
           + ") VALUES (:aid, :cid, :tid, :evt, :chan"
           + (", :dr" if dismiss_reason else "")
           + (", :cv" if conversion_value else "")
           + (", :cr" if conversion_ref else "")
           + (", :meta" if metadata else "")
           + (", :sid" if sid else "")
           + ")")

    params = [
        {"name": "aid", "value": {"stringValue": action_id}},
        {"name": "cid", "value": {"stringValue": customer_id}},
        {"name": "tid", "value": {"stringValue": template_id}},
        {"name": "evt", "value": {"stringValue": evt}},
        {"name": "chan", "value": {"stringValue": channel}},
    ]
    if dismiss_reason:
        params.append({"name": "dr", "value": {"stringValue": dismiss_reason}})
    if conversion_value:
        params.append({"name": "cv", "value": {"doubleValue": float(conversion_value)}})
    if conversion_ref:
        params.append({"name": "cr", "value": {"stringValue": conversion_ref}})
    if metadata:
        params.append({"name": "meta", "value": {"stringValue": metadata}})
    if sid:
        params.append({"name": "sid", "value": {"stringValue": sid}})

    _sql(sql, params)

    # Update action status if dismissed or converted
    if evt == "dismissed":
        _sql("UPDATE next_best_actions SET status='dismissed', dismissed_at=NOW() WHERE action_id=:aid",
             [{"name": "aid", "value": {"stringValue": action_id}}])
    elif evt == "converted":
        _sql("UPDATE next_best_actions SET status='converted', converted_at=NOW() WHERE action_id=:aid",
             [{"name": "aid", "value": {"stringValue": action_id}}])
    elif evt == "viewed":
        _sql("UPDATE next_best_actions SET view_count=view_count+1, last_viewed_at=NOW(), first_viewed_at=COALESCE(first_viewed_at, NOW()) WHERE action_id=:aid",
             [{"name": "aid", "value": {"stringValue": action_id}}])

    return _resp(200, {"ok": True, "action_id": action_id, "event": evt})


def _get_action_explain(event, path):
    """GET /sessions/me/actions/{action_id}/explain — full explainability payload."""
    sid = _extract_sid(event)
    if not sid:
        return _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return _resp(404, {"error": "No banking profile found"})

    # Extract action_id from path: /sessions/me/actions/{action_id}/explain
    parts = path.strip("/").split("/")
    if len(parts) < 5:
        return _resp(400, {"error": "Invalid path"})
    action_id = parts[3]

    import json as _json

    # Get NBA + template + FHS in one query set
    action_rows = _sql(
        "SELECT a.action_id, a.title, a.reasoning, a.metrics, a.confidence, "
        "a.source, a.source_detail, a.model_version, a.generated_at, "
        "a.template_id, a.category, a.priority, "
        "t.template_name, t.eligibility_rules, t.default_priority "
        "FROM next_best_actions a "
        "JOIN nba_templates t ON a.template_id = t.template_id "
        "WHERE a.action_id = :aid AND a.customer_id = :cid",
        [{"name": "aid", "value": {"stringValue": action_id}},
         {"name": "cid", "value": {"stringValue": customer_id}}]
    )
    if not action_rows:
        return _resp(404, {"error": "Action not found"})

    r = action_rows[0]
    def v(i):
        cell = r[i]
        if cell.get("isNull"): return None
        return cell.get("stringValue") or cell.get("longValue") or cell.get("doubleValue")

    # Get FHS
    fhs_rows = _sql(
        "SELECT score, band, subscore_debt, subscore_savings, subscore_spending, "
        "subscore_income, subscore_credit, subscore_behavior "
        "FROM customer_financial_health WHERE customer_id = :cid",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    fhs = {}
    if fhs_rows:
        fr = fhs_rows[0]
        fv = lambda i: fr[i].get("longValue") or fr[i].get("stringValue") if not fr[i].get("isNull") else None
        fhs = {"score": fv(0), "band": fv(1), "subscores": {
            "debt": fv(2), "savings": fv(3), "spending": fv(4),
            "income": fv(5), "credit": fv(6), "behavior": fv(7)}}

    # Get activity log
    activity_rows = _sql(
        "SELECT event, channel, created_at, dismiss_reason FROM nba_interactions "
        "WHERE action_id = :aid ORDER BY created_at",
        [{"name": "aid", "value": {"stringValue": action_id}}]
    )
    activity = []
    for ar in activity_rows:
        activity.append({
            "event": list(ar[0].values())[0] if not ar[0].get("isNull") else None,
            "channel": list(ar[1].values())[0] if not ar[1].get("isNull") else None,
            "timestamp": str(list(ar[2].values())[0])[:19] if not ar[2].get("isNull") else None,
            "reason": list(ar[3].values())[0] if not ar[3].get("isNull") else None,
        })

    # Get customer profile summary
    profile_rows = _sql(
        "SELECT first_name, credit_score, nationality FROM customers WHERE customer_id = :cid",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    profile = {}
    if profile_rows:
        pr = profile_rows[0]
        profile = {
            "first_name": list(pr[0].values())[0] if not pr[0].get("isNull") else None,
            "credit_score": list(pr[1].values())[0] if not pr[1].get("isNull") else None,
            "nationality": list(pr[2].values())[0] if not pr[2].get("isNull") else None,
        }

    # Parse JSON fields
    metrics = None
    try: metrics = _json.loads(v(3)) if v(3) else None
    except: pass
    eligibility = None
    try: eligibility = _json.loads(v(13)) if v(13) else None
    except: pass

    return _resp(200, {
        "action_id": v(0),
        "title": v(1),
        "reasoning": v(2),
        "metrics": metrics,
        "confidence": float(v(4)) if v(4) else None,
        "category": v(10),
        "priority": v(11),
        "generation": {
            "source": v(5),
            "source_detail": v(6),
            "model": v(7),
            "generated_at": str(v(8))[:19] if v(8) else None,
            "template_name": v(12),
            "template_id": v(9),
        },
        "eligibility_rules": eligibility,
        "customer_profile": profile,
        "financial_health": fhs,
        "activity_log": activity,
    })


# =============================================================================
# NBA Preferences (V015 nba_suppressions table) — bidirectional suppression
# =============================================================================
#
# Customers can:
#   • Hide an entire category (e.g. "opportunity")
#   • Hide a specific template (e.g. "home_loan_prequalification")
#   • Hide all NBAs (global opt-out)
#
# Suppressions are NEVER hard-deleted. Re-enabling flips status='active' and
# sets lifted_at/lifted_source. This preserves a 7-year audit trail for
# compliance (BRD §11 — explainability & traceability; BR-7.5 — customer
# control).
#
# Default UX pattern is time-bound (30 days / 6 months) — prevents customers
# from accidentally hiding recommendations forever.
# =============================================================================

_ALLOWED_SUPPRESSED_SOURCES = {
    "explain_panel", "preferences_page", "dismiss_banner", "alma_chat", "admin"
}
_ALLOWED_LIFTED_SOURCES = {
    "preferences_page", "for_you_banner", "alma_chat", "admin", "auto_expire"
}
_ALLOWED_SCOPE_TYPES = {"category", "template", "all"}
_ALLOWED_CATEGORIES = {
    "opportunity", "wellness", "security", "profile",
    "loyalty", "servicing", "retention"
}


def _require_customer(event):
    """Shared auth+profile resolution helper."""
    sid = _extract_sid(event)
    if not sid:
        return None, _resp(401, {"error": "no session"})
    item = _load_session(sid)
    if not item:
        return None, _resp(401, {"error": "invalid session"})
    customer_id = _resolve_customer_id_by_sub(item.get("cognito_sub", ""))
    if not customer_id:
        return None, _resp(404, {"error": "No banking profile found"})
    return customer_id, None


def _list_nba_preferences(event):
    """GET /sessions/me/nba-preferences

    Returns all suppressions for the authenticated customer — both currently
    active (status='suppressed', not expired) and historical (status='active'
    after a re-enable). The UI groups them accordingly.
    """
    customer_id, err = _require_customer(event)
    if err:
        return err

    rows = _sql(
        "SELECT suppression_id, scope_type, scope_value, status, "
        "       suppressed_at, suppressed_reason, suppressed_source, "
        "       expires_at, lifted_at, lifted_source "
        "  FROM nba_suppressions "
        " WHERE customer_id = :cid "
        " ORDER BY (status = 'suppressed') DESC, suppressed_at DESC",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )

    def _cell(c):
        if c.get("isNull"):
            return None
        return c.get("stringValue") or c.get("longValue") or c.get("doubleValue")

    active = []
    history = []
    now = int(time.time())
    for r in rows:
        rec = {
            "preference_id": _cell(r[0]),
            "scope_type": _cell(r[1]),
            "scope_value": _cell(r[2]),
            "status": _cell(r[3]),
            "suppressed_at": str(_cell(r[4]))[:19] if _cell(r[4]) else None,
            "reason": _cell(r[5]),
            "source": _cell(r[6]),
            "expires_at": str(_cell(r[7]))[:19] if _cell(r[7]) else None,
            "lifted_at": str(_cell(r[8]))[:19] if _cell(r[8]) else None,
            "lifted_source": _cell(r[9]),
        }
        # Expired suppressions are effectively active from the customer's POV
        is_expired = False
        if rec["status"] == "suppressed" and rec["expires_at"]:
            try:
                exp = time.mktime(time.strptime(rec["expires_at"], "%Y-%m-%d %H:%M:%S"))
                is_expired = exp < now
            except Exception:
                is_expired = False
        rec["effectively_active"] = (rec["status"] == "suppressed") and not is_expired
        if rec["effectively_active"]:
            active.append(rec)
        else:
            history.append(rec)

    return _resp(200, {
        "customer_id": customer_id,
        "active_suppressions": active,
        "history": history,
        "counts": {"active": len(active), "history": len(history)},
    })


def _create_nba_preference(event):
    """POST /sessions/me/nba-preferences

    Body:
      {
        "scope_type":  "category" | "template" | "all",
        "scope_value": "opportunity" | "home_loan_prequalification" | "*",
        "duration_days": 30 | 180 | null,   # null = indefinite
        "reason":      "not_interested" | "wrong_time" | "already_did" | ...,
        "source":      "explain_panel" | "preferences_page" | ...
      }

    Upsert semantics: if an existing (customer, scope) row is already 'active',
    it's flipped back to 'suppressed' with fresh timestamps. If it's already
    'suppressed', its expires_at is refreshed.
    """
    customer_id, err = _require_customer(event)
    if err:
        return err

    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _resp(400, {"error": "Invalid JSON body"})

    scope_type = (body.get("scope_type") or "").strip().lower()
    scope_value = (body.get("scope_value") or "").strip().lower()
    duration_days = body.get("duration_days")
    reason = (body.get("reason") or "").strip()[:200] or None
    source = (body.get("source") or "explain_panel").strip().lower()

    if scope_type not in _ALLOWED_SCOPE_TYPES:
        return _resp(400, {"error": f"scope_type must be one of {sorted(_ALLOWED_SCOPE_TYPES)}"})
    if not scope_value:
        return _resp(400, {"error": "scope_value is required"})
    if source not in _ALLOWED_SUPPRESSED_SOURCES:
        return _resp(400, {"error": f"source must be one of {sorted(_ALLOWED_SUPPRESSED_SOURCES)}"})
    if scope_type == "category" and scope_value not in _ALLOWED_CATEGORIES:
        return _resp(400, {"error": f"category must be one of {sorted(_ALLOWED_CATEGORIES)}"})
    if scope_type == "all" and scope_value != "*":
        scope_value = "*"
    if duration_days is not None:
        try:
            duration_days = int(duration_days)
            if duration_days <= 0 or duration_days > 3650:
                return _resp(400, {"error": "duration_days must be between 1 and 3650"})
        except (TypeError, ValueError):
            return _resp(400, {"error": "duration_days must be an integer"})

    # UPSERT: unique key is (customer_id, scope_type, scope_value)
    if duration_days:
        sql = (
            "INSERT INTO nba_suppressions "
            "  (customer_id, scope_type, scope_value, status, suppressed_at, "
            "   suppressed_reason, suppressed_source, expires_at, lifted_at, lifted_source) "
            "VALUES "
            "  (:cid, :st, :sv, 'suppressed', NOW(), :reason, :source, "
            "   DATE_ADD(NOW(), INTERVAL :days DAY), NULL, NULL) "
            "ON DUPLICATE KEY UPDATE "
            "  status='suppressed', suppressed_at=NOW(), "
            "  suppressed_reason=:reason, suppressed_source=:source, "
            "  expires_at=DATE_ADD(NOW(), INTERVAL :days DAY), "
            "  lifted_at=NULL, lifted_source=NULL"
        )
        params = [
            {"name": "cid", "value": {"stringValue": customer_id}},
            {"name": "st", "value": {"stringValue": scope_type}},
            {"name": "sv", "value": {"stringValue": scope_value}},
            {"name": "reason", "value": {"stringValue": reason} if reason else {"isNull": True}},
            {"name": "source", "value": {"stringValue": source}},
            {"name": "days", "value": {"longValue": duration_days}},
        ]
    else:
        sql = (
            "INSERT INTO nba_suppressions "
            "  (customer_id, scope_type, scope_value, status, suppressed_at, "
            "   suppressed_reason, suppressed_source, expires_at, lifted_at, lifted_source) "
            "VALUES "
            "  (:cid, :st, :sv, 'suppressed', NOW(), :reason, :source, NULL, NULL, NULL) "
            "ON DUPLICATE KEY UPDATE "
            "  status='suppressed', suppressed_at=NOW(), "
            "  suppressed_reason=:reason, suppressed_source=:source, "
            "  expires_at=NULL, lifted_at=NULL, lifted_source=NULL"
        )
        params = [
            {"name": "cid", "value": {"stringValue": customer_id}},
            {"name": "st", "value": {"stringValue": scope_type}},
            {"name": "sv", "value": {"stringValue": scope_value}},
            {"name": "reason", "value": {"stringValue": reason} if reason else {"isNull": True}},
            {"name": "source", "value": {"stringValue": source}},
        ]

    _sql(sql, params)

    # Return the resulting row for the UI
    rows = _sql(
        "SELECT suppression_id, expires_at FROM nba_suppressions "
        "WHERE customer_id=:cid AND scope_type=:st AND scope_value=:sv",
        [
            {"name": "cid", "value": {"stringValue": customer_id}},
            {"name": "st", "value": {"stringValue": scope_type}},
            {"name": "sv", "value": {"stringValue": scope_value}},
        ]
    )
    pref_id = None
    expires_at = None
    if rows:
        r = rows[0]
        pref_id = r[0].get("longValue")
        if not r[1].get("isNull"):
            expires_at = str(list(r[1].values())[0])[:19]

    return _resp(201, {
        "preference_id": pref_id,
        "customer_id": customer_id,
        "scope_type": scope_type,
        "scope_value": scope_value,
        "duration_days": duration_days,
        "expires_at": expires_at,
        "status": "suppressed",
        "reason": reason,
        "source": source,
    })


def _delete_nba_preference(event, path):
    """DELETE /sessions/me/nba-preferences/{preference_id}

    Logical delete — flips status to 'active' and sets lifted_at/lifted_source.
    The row is preserved for audit. If the preference_id does not belong to
    the authenticated customer, returns 404 (no information leak).

    Optional query string:  ?source=preferences_page|for_you_banner|alma_chat
    """
    customer_id, err = _require_customer(event)
    if err:
        return err

    parts = path.strip("/").split("/")
    # /sessions/me/nba-preferences/{id} → parts = ['sessions','me','nba-preferences','{id}']
    if len(parts) < 4:
        return _resp(400, {"error": "Missing preference_id"})
    pref_id_raw = parts[3]
    try:
        pref_id = int(pref_id_raw)
    except ValueError:
        return _resp(400, {"error": "preference_id must be an integer"})

    qs = event.get("queryStringParameters") or {}
    source = (qs.get("source") or "preferences_page").strip().lower()
    if source not in _ALLOWED_LIFTED_SOURCES:
        source = "preferences_page"

    # Ownership + state check
    rows = _sql(
        "SELECT status FROM nba_suppressions "
        "WHERE suppression_id=:pid AND customer_id=:cid",
        [
            {"name": "pid", "value": {"longValue": pref_id}},
            {"name": "cid", "value": {"stringValue": customer_id}},
        ]
    )
    if not rows:
        return _resp(404, {"error": "Preference not found"})
    current_status = list(rows[0][0].values())[0]
    if current_status == "active":
        return _resp(200, {"preference_id": pref_id, "status": "active", "already_active": True})

    _sql(
        "UPDATE nba_suppressions "
        "   SET status='active', lifted_at=NOW(), lifted_source=:src "
        " WHERE suppression_id=:pid AND customer_id=:cid",
        [
            {"name": "src", "value": {"stringValue": source}},
            {"name": "pid", "value": {"longValue": pref_id}},
            {"name": "cid", "value": {"stringValue": customer_id}},
        ]
    )

    return _resp(200, {
        "preference_id": pref_id,
        "status": "active",
        "lifted_source": source,
    })


def _get_financial_health(event):
    """GET /sessions/me/financial-health — FHS score, subscores, explanation, peer benchmark."""
    customer_id, err = _require_customer(event)
    if err:
        return err

    rows = _sql(
        "SELECT score, band, subscore_debt, subscore_savings, subscore_spending, "
        "subscore_income, subscore_credit, subscore_behavior, explanation, "
        "calculated_at, calculation_source "
        "FROM customer_financial_health WHERE customer_id = :cid",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    if not rows:
        return _resp(404, {"error": "Financial Health Score not yet computed"})

    r = rows[0]
    def v(i):
        cell = r[i]
        if cell.get("isNull"): return None
        return cell.get("stringValue") or cell.get("longValue") or cell.get("doubleValue")

    subscores = {
        "debt": v(2), "savings": v(3), "spending": v(4),
        "income": v(5), "credit": v(6), "behavior": v(7),
    }
    weakest = min(subscores.items(), key=lambda x: x[1] if x[1] else 999)
    strongest = max(subscores.items(), key=lambda x: x[1] if x[1] else 0)

    # Peer benchmark (avg FHS for all customers)
    peer = _sql("SELECT AVG(score) FROM customer_financial_health", [])
    peer_avg = round(float(list(peer[0][0].values())[0]), 1) if peer else 72

    # Related improvement NBAs (wellness category, active for this customer)
    improvement_rows = _sql(
        "SELECT action_id, title, priority FROM next_best_actions "
        "WHERE customer_id=:cid AND status='active' AND category='wellness' "
        "ORDER BY priority DESC LIMIT 3",
        [{"name": "cid", "value": {"stringValue": customer_id}}]
    )
    improvements = []
    for ir in improvement_rows:
        improvements.append({
            "action_id": list(ir[0].values())[0] if not ir[0].get("isNull") else None,
            "title": list(ir[1].values())[0] if not ir[1].get("isNull") else None,
            "priority": list(ir[2].values())[0] if not ir[2].get("isNull") else None,
        })

    return _resp(200, {
        "customer_id": customer_id,
        "score": v(0),
        "band": v(1),
        "subscores": subscores,
        "weakest_area": {"name": weakest[0], "score": weakest[1]},
        "strongest_area": {"name": strongest[0], "score": strongest[1]},
        "explanation": v(8),
        "calculated_at": str(v(9))[:19] if v(9) else None,
        "source": v(10),
        "peer_average": peer_avg,
        "improvement_actions": improvements,
    })


def _sso_login(event):
    """POST /sessions/sso — create session from Identity Center SSO tokens."""
    body = json.loads(event.get("body") or "{}")
    print(f"[SSO] Body keys: {list(body.keys())}, code={body.get('code','')[:20]}, redirect={body.get('redirect_uri','')}")
    email = body.get("email", "").strip()
    name = body.get("name", "").strip()
    groups = body.get("groups", "").strip()
    portal = body.get("portal", "employee")
    id_token = body.get("id_token", "")
    code = body.get("code", "")
    redirect_uri = body.get("redirect_uri", "")

    # If we got an auth code (from Vite OAuth callback), exchange it for tokens
    if code and not id_token:
        import urllib.request, urllib.parse as _up
        cognito_domain = "https://aibank-employee.auth.eu-west-1.amazoncognito.com"
        client_id = "1po4o2jg5sso8221vpo7j27s9m"
        try:
            data = _up.urlencode({
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": code,
                "redirect_uri": redirect_uri
            }).encode()
            req = urllib.request.Request(f"{cognito_domain}/oauth2/token", data=data,
                headers={"Content-Type": "application/x-www-form-urlencoded"})
            resp_data = urllib.request.urlopen(req, timeout=10).read()
            tokens = json.loads(resp_data)
            id_token = tokens.get("id_token", "")
            print(f"[SSO] Token exchange OK, id_token={id_token[:30]}...")
        except Exception as e:
            print(f"[SSO] Token exchange FAILED: {e}")
            return _resp(401, {"error": f"Token exchange failed: {str(e)[:200]}"})

    # Decode the id_token BEFORE checking email (token has the email claim)
    if id_token:
        try:
            payload = json.loads(base64.b64decode(id_token.split(".")[1] + "==").decode())
            email = payload.get("email", email) or email
            name = payload.get("name", "") or payload.get("cognito:username", "") or name
            groups = payload.get("custom:groups", groups)
            print(f"[SSO] Token decoded: email={email}, name={name}, groups={groups[:80] if groups else 'none'}")
        except Exception as e:
            print(f"[SSO] Token decode failed: {e}")

    if not email:
        return _resp(400, {"error": "email required (no email in token)"})

    # Determine role from DynamoDB config (AD group → role)
    # Groups come as URL-encoded, possibly multiple:
    # "[demoaws.com%2F%2FS-1-5-21-...-125632, demoaws.com%2F%2FS-1-5-21-...-125624]"
    import urllib.parse
    role = "employee"
    if groups:
        decoded = urllib.parse.unquote(groups).strip("[] ")
        for g in decoded.split(","):
            g = g.strip()
            if "//" in g:
                sid = g.split("//")[1].lower()
                found_role = _get_role_from_config(sid)
                if found_role != sid:
                    role = found_role
                    break
            elif "@" in g:
                gname = g.split("@")[0].lower()
                found_role = _get_role_from_config(gname)
                if found_role != gname:
                    role = found_role
                    break


    # Create session (same as regular login)
    sub = f"sso-{email}"
    return _create_session(email, name or email.split("@")[0], sub, "", portal, "BH", role=role)


def _reset_nba(event):
    """POST /sessions/me/reset-nba — removes all real-time NBAs and purchased products for retesting."""
    customer_id, err = _require_customer(event)
    if err:
        return err

    # Delete real-time NBAs
    _sql("DELETE FROM next_best_actions WHERE customer_id=:cid AND source='agent'",
         [{"name": "cid", "value": {"stringValue": customer_id}}])

    # Delete purchased products
    _sql("DELETE FROM customer_products WHERE customer_id=:cid",
         [{"name": "cid", "value": {"stringValue": customer_id}}])

    # Delete life events
    _sql("DELETE FROM customer_life_events WHERE customer_id=:cid",
         [{"name": "cid", "value": {"stringValue": customer_id}}])

    # Reverse NBA-initiated transactions (purchases made via execute_purchase)
    # These have description containing 'Insurance' or 'Goal Saver' or match product purchases
    _sql("DELETE FROM transactions WHERE account_id IN "
         "(SELECT account_id FROM accounts WHERE customer_id=:cid) "
         "AND description LIKE '%Purchase%'",
         [{"name": "cid", "value": {"stringValue": customer_id}}])

    # Restore account balance by recalculating from remaining transactions
    # Simpler: just reverse the product purchase amounts
    _sql("UPDATE accounts SET balance = balance + "
         "(SELECT COALESCE(SUM(amount_bhd),0) FROM customer_products WHERE customer_id=:cid) "
         "WHERE customer_id=:cid AND account_type='CURRENT'",
         [{"name": "cid", "value": {"stringValue": customer_id}}])

    return _resp(200, {"ok": True, "message": "NBA data reset. Real-time recommendations, products, life events, and related transactions cleared."})

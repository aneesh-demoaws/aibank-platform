"""Transaction Execution Module — handles real financial transactions.

Supports:
- Product purchases (travel insurance, FD, etc.)
- Loan disbursements (credit to account after approval)
- Internal transfers (between customer accounts)
- Standing instructions (recurring debits)

All transactions:
1. Validate balance (for debits)
2. Execute the transaction (INSERT into transactions + UPDATE balance)
3. Create product record (if applicable)
4. Return receipt

Used by: Execution Agent, Loan Agent, NBA Agent
"""
import json, uuid, boto3
from datetime import datetime, timezone

rds = boto3.client('rds-data', region_name='eu-west-1')
CLUSTER = "arn:aws:rds:eu-west-1:519124228967:cluster:aibank-core-banking-dr"
SECRET = "arn:aws:secretsmanager:eu-west-1:519124228967:secret:aibank-core-banking-CQeAg6"
DB = "corebanking"


def _sql(sql, params=None):
    kwargs = dict(resourceArn=CLUSTER, secretArn=SECRET, database=DB, sql=sql)
    if params:
        kwargs['parameters'] = params
    return rds.execute_statement(**kwargs)


def _val(cell):
    if cell.get('isNull'):
        return None
    return list(cell.values())[0]


def get_primary_account(customer_id):
    """Get the customer's primary (highest balance) active account."""
    rows = _sql(
        "SELECT account_id, balance FROM accounts WHERE customer_id=:cid AND status='ACTIVE' ORDER BY balance DESC LIMIT 1",
        [{'name': 'cid', 'value': {'stringValue': customer_id}}]
    ).get('records', [])
    if not rows:
        return None, 0
    return _val(rows[0][0]), float(_val(rows[0][1]) or 0)


def execute_debit(customer_id, amount, description, merchant_name, category='purchase'):
    """Debit customer account — checks balance, creates transaction, updates balance."""
    account_id, balance = get_primary_account(customer_id)
    if not account_id:
        return {'success': False, 'error': 'No active account found'}
    if balance < amount:
        return {'success': False, 'error': f'Insufficient balance. Available: BHD {balance:.3f}, Required: BHD {amount:.3f}'}

    txn_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"

    # Insert transaction
    _sql(
        "INSERT INTO transactions (transaction_id, account_id, transaction_type, amount, "
        "description, merchant_name, transaction_date, balance_after, value_date) "
        "VALUES (:tid, :aid, 'debit', :amt, :desc, :merch, NOW(), :bal, CURDATE())",
        [
            {'name': 'tid', 'value': {'stringValue': txn_id}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'desc', 'value': {'stringValue': description}},
            {'name': 'merch', 'value': {'stringValue': merchant_name}},
            {'name': 'bal', 'value': {'doubleValue': balance - amount}},
        ]
    )

    # Update balance
    _sql(
        "UPDATE accounts SET balance = balance - :amt WHERE account_id = :aid",
        [
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
        ]
    )

    new_balance = balance - amount
    return {
        'success': True,
        'transaction_id': txn_id,
        'account_id': account_id,
        'amount_debited': amount,
        'new_balance': round(new_balance, 3),
    }


def execute_credit(customer_id, amount, description, merchant_name):
    """Credit customer account — creates transaction, updates balance."""
    account_id, balance = get_primary_account(customer_id)
    if not account_id:
        return {'success': False, 'error': 'No active account found'}

    txn_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"

    _sql(
        "INSERT INTO transactions (transaction_id, account_id, transaction_type, amount, "
        "description, merchant_name, transaction_date, balance_after, value_date) "
        "VALUES (:tid, :aid, 'credit', :amt, :desc, :merch, NOW(), :bal, CURDATE())",
        [
            {'name': 'tid', 'value': {'stringValue': txn_id}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'desc', 'value': {'stringValue': description}},
            {'name': 'merch', 'value': {'stringValue': merchant_name}},
            {'name': 'bal', 'value': {'doubleValue': balance + amount}},
        ]
    )

    _sql(
        "UPDATE accounts SET balance = balance + :amt WHERE account_id = :aid",
        [
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
        ]
    )

    return {
        'success': True,
        'transaction_id': txn_id,
        'account_id': account_id,
        'amount_credited': amount,
        'new_balance': round(balance + amount, 3),
    }


def purchase_product(customer_id, product_type, product_name, amount, details=None, expires_at=None, source_nba_id=None):
    """Purchase a product — debit account + create product record."""
    # Lookup price from catalog if amount not provided
    if not amount or amount == 0:
        rows = _sql(
            "SELECT product_name, price_bhd FROM product_catalog WHERE product_type=:pt AND status='active'",
            [{'name': 'pt', 'value': {'stringValue': product_type}}]
        ).get('records', [])
        if rows:
            product_name = _val(rows[0][0]) or product_name
            amount = float(_val(rows[0][1]) or 0)
    
    if amount <= 0:
        # Free product — still create the product record
        product_id = f"PRD-{uuid.uuid4().hex[:8].upper()}"
        receipt_id = f"RCP-{uuid.uuid4().hex[:8].upper()}"
        _sql(
            "INSERT INTO customer_products (product_id, customer_id, product_type, product_name, "
            "amount_bhd, status, details, purchased_at, source_nba_id, receipt_id) "
            "VALUES (:pid, :cid, :ptype, :pname, 0, 'active', :details, NOW(), :nba, :rcpt)",
            [
                {'name': 'pid', 'value': {'stringValue': product_id}},
                {'name': 'cid', 'value': {'stringValue': customer_id}},
                {'name': 'ptype', 'value': {'stringValue': product_type}},
                {'name': 'pname', 'value': {'stringValue': product_name}},
                {'name': 'details', 'value': {'stringValue': json.dumps(details or {})}},
                {'name': 'nba', 'value': {'stringValue': source_nba_id} if source_nba_id else {'isNull': True}},
                {'name': 'rcpt', 'value': {'stringValue': receipt_id}},
            ]
        )
        return {'success': True, 'product_id': product_id, 'receipt_id': receipt_id, 'amount': 0, 'note': 'Free product activated'}

    # Debit
    debit_result = execute_debit(
        customer_id, amount,
        description=f"{product_name} - Purchase",
        merchant_name="AI Bank Products"
    )
    if not debit_result['success']:
        return debit_result

    # Create product record
    product_id = f"PRD-{uuid.uuid4().hex[:8].upper()}"
    receipt_id = f"RCP-{uuid.uuid4().hex[:8].upper()}"

    _sql(
        "INSERT INTO customer_products (product_id, customer_id, product_type, product_name, "
        "amount_bhd, status, details, purchased_at, expires_at, source_nba_id, receipt_id) "
        "VALUES (:pid, :cid, :ptype, :pname, :amt, 'active', :details, NOW(), :exp, :nba, :rcpt)",
        [
            {'name': 'pid', 'value': {'stringValue': product_id}},
            {'name': 'cid', 'value': {'stringValue': customer_id}},
            {'name': 'ptype', 'value': {'stringValue': product_type}},
            {'name': 'pname', 'value': {'stringValue': product_name}},
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'details', 'value': {'stringValue': json.dumps(details or {})}},
            {'name': 'exp', 'value': {'stringValue': expires_at} if expires_at else {'isNull': True}},
            {'name': 'nba', 'value': {'stringValue': source_nba_id} if source_nba_id else {'isNull': True}},
            {'name': 'rcpt', 'value': {'stringValue': receipt_id}},
        ]
    )

    return {
        'success': True,
        'product_id': product_id,
        'receipt_id': receipt_id,
        'transaction_id': debit_result['transaction_id'],
        'amount': amount,
        'new_balance': debit_result['new_balance'],
        'product_name': product_name,
    }


def disburse_loan(customer_id, loan_amount, application_id, loan_type):
    """Disburse approved loan — credit customer account.

    Idempotent: if a disbursement transaction for this application_id already
    exists in the transactions table (matched via reference_number), returns
    that prior result instead of creating a duplicate credit.
    """
    # Idempotency: check for existing disbursement on this application
    existing = _sql(
        "SELECT t.transaction_id, t.amount, t.balance_after, t.account_id "
        "FROM transactions t "
        "WHERE t.reference_number = :ref AND t.transaction_type = 'credit' "
        "ORDER BY t.transaction_date DESC LIMIT 1",
        [{'name': 'ref', 'value': {'stringValue': f'LOAN-{application_id}'}}]
    )
    if existing.get('records'):
        r = existing['records'][0]
        prior_txn = _val(r[0])
        prior_amt = _val(r[1])
        prior_bal = _val(r[2])
        return {
            'success': True,
            'transaction_id': prior_txn,
            'amount_disbursed': float(prior_amt) if prior_amt else loan_amount,
            'new_balance': float(prior_bal) if prior_bal else None,
            'application_id': application_id,
            'idempotent': True,
            'message': 'Disbursement already executed for this application',
        }

    credit_result = execute_credit_with_ref(
        customer_id, loan_amount,
        description=f"Loan Disbursement - {loan_type} ({application_id})",
        merchant_name="AI Bank Lending",
        reference_number=f"LOAN-{application_id}"
    )
    if not credit_result['success']:
        return credit_result

    txn_id = credit_result['transaction_id']

    # Stamp loan_applications row with disbursement details (best-effort)
    try:
        _sql(
            "UPDATE loan_applications "
            "SET disbursement_txn_id = :tid, disbursed_at = NOW(), disbursement_status = 'success' "
            "WHERE application_id = :aid",
            [
                {'name': 'tid', 'value': {'stringValue': txn_id}},
                {'name': 'aid', 'value': {'stringValue': application_id}},
            ]
        )
    except Exception as e:
        # Don't fail the disbursement if the stamp fails — money is already in account
        import logging
        logging.getLogger().warning(f"Failed to stamp loan_applications for {application_id}: {e}")

    return {
        'success': True,
        'transaction_id': txn_id,
        'amount_disbursed': loan_amount,
        'new_balance': credit_result['new_balance'],
        'application_id': application_id,
        'idempotent': False,
    }


def execute_credit_with_ref(customer_id, amount, description, merchant_name, reference_number):
    """Credit with a reference_number for idempotency tracking."""
    account_id, balance = get_primary_account(customer_id)
    if not account_id:
        return {'success': False, 'error': 'No active account found'}

    txn_id = f"TXN-{uuid.uuid4().hex[:12].upper()}"

    _sql(
        "INSERT INTO transactions (transaction_id, account_id, transaction_type, amount, "
        "description, merchant_name, reference_number, transaction_date, balance_after, value_date, status) "
        "VALUES (:tid, :aid, 'credit', :amt, :desc, :merch, :ref, NOW(), :bal, CURDATE(), 'completed')",
        [
            {'name': 'tid', 'value': {'stringValue': txn_id}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'desc', 'value': {'stringValue': description}},
            {'name': 'merch', 'value': {'stringValue': merchant_name}},
            {'name': 'ref', 'value': {'stringValue': reference_number}},
            {'name': 'bal', 'value': {'doubleValue': balance + amount}},
        ]
    )

    _sql(
        "UPDATE accounts SET balance = balance + :amt WHERE account_id = :aid",
        [
            {'name': 'amt', 'value': {'doubleValue': amount}},
            {'name': 'aid', 'value': {'stringValue': account_id}},
        ]
    )

    return {
        'success': True,
        'transaction_id': txn_id,
        'account_id': account_id,
        'amount_credited': amount,
        'new_balance': round(balance + amount, 3),
    }


def reverse_disbursement(customer_id, application_id):
    """Reverse a loan disbursement by DELETING the original credit transaction
    and adjusting the account balance back. Per product decision: cleaner UI,
    no audit trail (Reset Loans is a developer/test feature).

    Returns success even if no prior disbursement found (idempotent reversal).
    """
    # Find the original disbursement
    existing = _sql(
        "SELECT t.transaction_id, t.amount, t.account_id "
        "FROM transactions t "
        "WHERE t.reference_number = :ref AND t.transaction_type = 'credit'",
        [{'name': 'ref', 'value': {'stringValue': f'LOAN-{application_id}'}}]
    )
    records = existing.get('records', [])
    if not records:
        return {
            'success': True,
            'reversed': False,
            'application_id': application_id,
            'message': 'No prior disbursement found — nothing to reverse',
        }

    deleted_txns = []
    total_reversed = 0.0
    account_id = None
    for r in records:
        txn_id = _val(r[0])
        amount = float(_val(r[1]) or 0)
        account_id = _val(r[2])

        # Delete the original credit transaction
        _sql(
            "DELETE FROM transactions WHERE transaction_id = :tid",
            [{'name': 'tid', 'value': {'stringValue': txn_id}}]
        )

        # Adjust account balance back down
        _sql(
            "UPDATE accounts SET balance = balance - :amt WHERE account_id = :aid",
            [
                {'name': 'amt', 'value': {'doubleValue': amount}},
                {'name': 'aid', 'value': {'stringValue': account_id}},
            ]
        )
        deleted_txns.append(txn_id)
        total_reversed += amount

    # Clear disbursement stamp on loan_applications (best-effort)
    try:
        _sql(
            "UPDATE loan_applications "
            "SET disbursement_txn_id = NULL, disbursed_at = NULL, disbursement_status = 'reversed' "
            "WHERE application_id = :aid",
            [{'name': 'aid', 'value': {'stringValue': application_id}}]
        )
    except Exception as e:
        import logging
        logging.getLogger().warning(f"Failed to clear loan_applications stamp for {application_id}: {e}")

    return {
        'success': True,
        'reversed': True,
        'application_id': application_id,
        'deleted_transaction_ids': deleted_txns,
        'amount_reversed': round(total_reversed, 3),
        'account_id': account_id,
    }


def handler(event, context):
    """Lambda handler — routes to the correct transaction type."""
    action = event.get('action', '')
    customer_id = event.get('customer_id', '')

    if not customer_id:
        return {'success': False, 'error': 'customer_id required'}

    if action == 'purchase':
        return purchase_product(
            customer_id=customer_id,
            product_type=event.get('product_type', 'other'),
            product_name=event.get('product_name', 'Product'),
            amount=float(event.get('amount', 0)),
            details=event.get('details'),
            expires_at=event.get('expires_at'),
            source_nba_id=event.get('source_nba_id'),
        )
    elif action == 'disburse_loan':
        return disburse_loan(
            customer_id=customer_id,
            loan_amount=float(event.get('amount', 0)),
            application_id=event.get('application_id', ''),
            loan_type=event.get('loan_type', 'Personal'),
        )
    elif action == 'reverse_disbursement':
        return reverse_disbursement(
            customer_id=customer_id,
            application_id=event.get('application_id', ''),
        )
    elif action == 'debit':
        return execute_debit(
            customer_id=customer_id,
            amount=float(event.get('amount', 0)),
            description=event.get('description', 'Debit'),
            merchant_name=event.get('merchant_name', 'AI Bank'),
        )
    elif action == 'credit':
        return execute_credit(
            customer_id=customer_id,
            amount=float(event.get('amount', 0)),
            description=event.get('description', 'Credit'),
            merchant_name=event.get('merchant_name', 'AI Bank'),
        )
    else:
        return {'success': False, 'error': f'Unknown action: {action}'}

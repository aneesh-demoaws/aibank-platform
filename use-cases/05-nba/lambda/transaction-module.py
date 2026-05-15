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
    """Disburse approved loan — credit customer account."""
    credit_result = execute_credit(
        customer_id, loan_amount,
        description=f"Loan Disbursement - {loan_type} ({application_id})",
        merchant_name="AI Bank Lending"
    )
    if not credit_result['success']:
        return credit_result

    return {
        'success': True,
        'transaction_id': credit_result['transaction_id'],
        'amount_disbursed': loan_amount,
        'new_balance': credit_result['new_balance'],
        'application_id': application_id,
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

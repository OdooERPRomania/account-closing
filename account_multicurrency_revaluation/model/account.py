# Copyright 2012-2018 Camptocamp SA
# License AGPL-3.0 or later (http://www.gnu.org/licenses/agpl).

from odoo import api, fields, models


class AccountAccountLine(models.Model):
    _inherit = "account.move.line"
    # By convention added columns start with gl_.
    gl_foreign_balance = fields.Float(string="Aggregated Amount currency")
    gl_balance = fields.Float(string="Aggregated Amount")
    gl_revaluated_balance = fields.Float(string="Revaluated Amount")
    gl_currency_rate = fields.Float(string="Currency rate")


class AccountAccount(models.Model):
    _inherit = "account.account"

    currency_revaluation = fields.Boolean(
        string="Allow Currency revaluation", default=False
    )

    def init(self):
        # all receivable, payable, Bank and Cash accounts should
        # have currency_revaluation True by default
        res = super().init()
        accounts = self.env["account.account"].search(
            [
                ("user_type_id.id", "in", self._get_revaluation_account_types()),
                ("currency_revaluation", "=", False),
            ]
        )
        accounts.write({"currency_revaluation": True})
        return res

    def _get_revaluation_account_types(self):
        return [
            self.env.ref("account.data_account_type_receivable").id,
            self.env.ref("account.data_account_type_payable").id,
            self.env.ref("account.data_account_type_liquidity").id,
        ]

    @api.onchange("user_type_id")
    def _onchange_user_type_id(self):
        revaluation_accounts = self._get_revaluation_account_types()
        for rec in self:
            if rec.user_type_id.id in revaluation_accounts:
                rec.currency_revaluation = True

    def _revaluation_query(self, revaluation_date,company_id):

# wrong is giving you a domain like 
#         tables, where_clause, where_clause_params = self.env[
#             "account.move.line"
#         ]._query_get()
        
        query = f"""
with amount as 
 ( SELECT aml.date,aml.account_id, aml.partner_id, aml.currency_id, aml.debit, aml.credit, aml.amount_currency
  FROM account_move_line aml
  INNER JOIN account_move am ON    
         aml.account_id IN {tuple(self.ids)} AND
         aml.display_type IS NULL AND  -- can be also without this
         aml.currency_id IS NOT NULL  AND        -- here is taking also last evaluation because they have the same currency, so the balance is ok
         aml.date <= '{revaluation_date}' AND
         (aml.company_id={company_id} OR aml.company_id=NULL) AND
         
         aml.move_id = am.id AND am.state = 'posted' --the state of lines is in account move 
          
  LEFT JOIN account_partial_reconcile aprc ON (aml.balance < 0 AND aml.id = aprc.credit_move_id) 
  LEFT JOIN account_move_line amlcf ON (aml.balance < 0 AND aprc.debit_move_id = amlcf.id AND amlcf.date <= '{revaluation_date}' ) 
  LEFT JOIN account_partial_reconcile aprd ON (aml.balance > 0 AND aml.id = aprd.debit_move_id) 
  LEFT JOIN account_move_line amldf ON (aml.balance > 0 AND aprd.credit_move_id = amldf.id AND amldf.date <= '{revaluation_date}' ) 
 
  GROUP  BY aml.id HAVING aml.full_reconcile_id IS NULL 
                      OR (MAX(amldf.id) IS NULL AND MAX(amlcf.id) IS NULL) -- is not partial reconciled 
 ) 

 SELECT account_id as id, partner_id, currency_id, --(before was _sql_mapping) just to show the sql result with 0 instead of null and with balance-debit-credit
     COALESCE(SUM(debit),0) - COALESCE(SUM(credit), 0) as balance, 
     COALESCE(SUM(debit), 0) as debit, 
     COALESCE(SUM(credit), 0) as credit, 
     COALESCE(SUM(amount_currency), 0) as foreign_balance 
    
    FROM amount   
    
    GROUP BY account_id, currency_id, partner_id
"""
        return query

    def compute_revaluations(self, revaluation_date,company_id):
        accounts = {}
        # compute for each account the balance/debit/credit from the move lines
        query = self._revaluation_query(revaluation_date,company_id)
        self.env.cr.execute(query)

        lines = self.env.cr.dictfetchall()
        rec_pay = [
            self.env.ref("account.data_account_type_receivable").id,
            self.env.ref("account.data_account_type_payable").id,
        ]

        for line in lines:
            # generate a tree
            # - account_id
            # -- currency_id
            # --- partner_id
            # ----- balances
            account_id, currency_id, partner_id = (
                line["id"],
                line["currency_id"],
                line["partner_id"],
            )
            account_type = self.env["account.account"].browse(account_id).user_type_id
            accounts.setdefault(account_id, {})
            partner_id = partner_id if account_type.id in rec_pay else False
            accounts[account_id].setdefault(partner_id, {})
            accounts[account_id][partner_id].setdefault(currency_id, {})
            accounts[account_id][partner_id][currency_id] = line

        return accounts


class AccountMove(models.Model):
    _inherit = "account.move"

    revaluation_to_reverse = fields.Boolean(
        string="revaluation to reverse", default=False
    )

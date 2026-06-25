from odoo import models, fields, api, _
from odoo.exceptions import UserError

class AccountJournal(models.Model):
    _inherit = 'account.journal'

    display_alias_fields = fields.Boolean(string="Display Alias Fields")

class PartnerMatching(models.Model):
    _name = 'partner.matching'
    _description = 'Partner Matching'
    _order = 'date desc, id desc'

    name = fields.Char(string="Reference", required=True, copy=False, readonly=True, 
                      default=lambda self: _('New'))
    state = fields.Selection([
        ('draft', 'Draft'),
        ('matched', 'Matched'),
    ], string="Status", default='draft', readonly=True, copy=False)
    
    date = fields.Date(string="Date", required=True, default=fields.Date.context_today)
    partner_id = fields.Many2one('res.partner', string="Partner", required=True, readonly=False)
    currency_id = fields.Many2one('res.currency', string="Currency", 
                                 default=lambda self: self.env.company.currency_id)

    # Store of selected move IDs to persist across filter changes
    selected_invoice_ids = fields.Many2many('account.move', 'matching_selected_inv_rel', string="Selected Invoices")
    selected_bill_ids = fields.Many2many('account.move', 'matching_selected_bill_rel', string="Selected Bills")

    # Selection lists on form
    line_invoice_ids = fields.One2many('partner.matching.line', 'matching_inv_id', string="Invoices")
    line_bill_ids = fields.One2many('partner.matching.line', 'matching_bill_id', string="Bills")

    # Live totals from the checkboxes
    total_selected_invoices = fields.Float(string="Selected Invoices Total", compute='_compute_selected_totals', digits=(16, 3))
    total_selected_bills = fields.Float(string="Selected Bills Total", compute='_compute_selected_totals', digits=(16, 3))
    
    netting_move_id = fields.Many2one('account.move', string="Netting Entry", readonly=True)
    opening_balance = fields.Monetary(string="Opening Balance", compute='_compute_opening_balance', currency_field='currency_id')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code('partner.matching') or _('NET/%s/%s') % (fields.Date.today().year, self.env['partner.matching'].search_count([]) + 1)
        return super().create(vals_list)

    @api.depends('line_invoice_ids.is_selected', 'line_bill_ids.is_selected', 'line_invoice_ids.manual_amount', 'line_bill_ids.manual_amount')
    def _compute_selected_totals(self):
        for rec in self:
            rec.total_selected_invoices = sum(abs(line.manual_amount) for line in rec.line_invoice_ids if line.is_selected)
            rec.total_selected_bills = sum(abs(line.manual_amount) for line in rec.line_bill_ids if line.is_selected)

    @api.depends('partner_id')
    def _compute_opening_balance(self):
        for rec in self:
            if not rec.partner_id:
                rec.opening_balance = 0.0
                continue
            
            # Identify "Opening Balance" moves: General journal entries (type 'entry')
            # with residual amounts for the partner in AR/AP accounts.
            lines = self.env['account.move.line'].search([
                ('partner_id', '=', rec.partner_id.id),
                ('account_id.account_type', 'in', ('asset_receivable', 'liability_payable')),
                ('parent_state', '=', 'posted'),
                ('reconciled', '=', False),
                ('move_id.move_type', '=', 'entry'),
            ])
            rec.opening_balance = sum(abs(l.amount_residual) for l in lines)

    def _rebuild_lines(self):
        """Shared method to rebuild invoice/bill lines based on current filters."""
        for rec in self:
            if rec.state != 'draft':
                continue
            if not rec.partner_id:
                rec.line_invoice_ids.unlink()
                rec.line_bill_ids.unlink()
                continue

            domain_base = [
                ('partner_id', 'child_of', rec.partner_id.id),
                ('state', '=', 'posted'),
            ]

            domain_base.append(('payment_state', 'in', ['not_paid', 'partial']))


            # Populate Invoices
            inv_domain = domain_base + [
                '|', 
                ('move_type', 'in', ('out_invoice', 'out_refund')),
                '&', ('move_type', '=', 'entry'), ('line_ids.account_id.account_type', '=', 'asset_receivable')
            ]
            docs_inv = rec.env['account.move'].search(inv_domain)
            
            # Filter out entries with 0 residual for the partner to keep the list clean
            docs_inv = docs_inv.filtered(lambda d: d.move_type != 'entry' or sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == rec.partner_id).mapped('amount_residual')) > 0)

            # Keep manual lines
            manual_inv_lines = rec.line_invoice_ids.filtered(lambda l: not l.move_id)
            rec.line_invoice_ids = [(5, 0, 0)]
            
            inv_vals = [(0, 0, {
                'matching_inv_id': rec.id,
                'move_id': d.id,
                'amount_total': abs(d.amount_total_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == rec.partner_id).mapped('balance'))),
                'amount_residual': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == rec.partner_id).mapped('amount_residual'))),
                'payment_state': d.payment_state,
                'invoice_date': d.invoice_date or d.date,
                'is_selected': d.id in rec.selected_invoice_ids.ids,
                'manual_amount': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == rec.partner_id).mapped('amount_residual'))),
            }) for d in docs_inv]
            
            # Restore manual lines
            for m_line in manual_inv_lines:
                inv_vals.append((0, 0, {
                    'matching_inv_id': rec.id,
                    'manual_ref': m_line.manual_ref,
                    'invoice_date': m_line.invoice_date,
                    'amount_total': m_line.amount_total,
                    'amount_residual': m_line.amount_residual,
                    'manual_amount': m_line.manual_amount,
                    'is_selected': m_line.is_selected,
                }))
            rec.write({'line_invoice_ids': inv_vals})

            # Populate Bills
            bill_domain = domain_base + [
                '|',
                ('move_type', 'in', ('in_invoice', 'in_refund')),
                '&', ('move_type', '=', 'entry'), ('line_ids.account_id.account_type', '=', 'liability_payable')
            ]
            docs_bill = rec.env['account.move'].search(bill_domain)
            
            # Filter out entries with 0 residual for the partner
            docs_bill = docs_bill.filtered(lambda d: d.move_type != 'entry' or sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == rec.partner_id).mapped('amount_residual')) > 0)

            # Keep manual lines
            manual_bill_lines = rec.line_bill_ids.filtered(lambda l: not l.move_id)
            rec.line_bill_ids = [(5, 0, 0)]
            
            bill_vals = [(0, 0, {
                'matching_bill_id': rec.id,
                'move_id': d.id,
                'amount_total': abs(d.amount_total_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == rec.partner_id).mapped('balance'))),
                'amount_residual': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == rec.partner_id).mapped('amount_residual'))),
                'payment_state': d.payment_state,
                'invoice_date': d.invoice_date or d.date,
                'is_selected': d.id in rec.selected_bill_ids.ids,
                'manual_amount': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == rec.partner_id).mapped('amount_residual'))),
            }) for d in docs_bill]
            
            # Restore manual lines
            for m_line in manual_bill_lines:
                bill_vals.append((0, 0, {
                    'matching_bill_id': rec.id,
                    'manual_ref': m_line.manual_ref,
                    'invoice_date': m_line.invoice_date,
                    'amount_total': m_line.amount_total,
                    'amount_residual': m_line.amount_residual,
                    'manual_amount': m_line.manual_amount,
                    'is_selected': m_line.is_selected,
                }))
            rec.write({'line_bill_ids': bill_vals})

    @api.onchange('partner_id')
    def _onchange_partner(self):
        """Rebuilds the lists whenever partner changes (unsaved records)."""
        if self.state != 'draft' or not self.partner_id:
            return

        domain_base = [
            ('partner_id', 'child_of', self.partner_id.id),
            ('state', '=', 'posted'),
        ]

        domain_base.append(('payment_state', 'in', ['not_paid', 'partial']))

        
        # Populate Invoices
        inv_domain = domain_base + [
            '|',
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            '&', ('move_type', '=', 'entry'), ('line_ids.account_id.account_type', '=', 'asset_receivable')
        ]
        docs_inv = self.env['account.move'].search(inv_domain)
        
        # Filter zero residual entries
        docs_inv = docs_inv.filtered(lambda d: d.move_type != 'entry' or sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == self.partner_id).mapped('amount_residual')) > 0)

        manual_inv_lines = self.line_invoice_ids.filtered(lambda l: not l.move_id)
        
        inv_vals = [(5, 0, 0)] + [(0, 0, {
            'move_id': d.id,
            'amount_total': abs(d.amount_total_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == self.partner_id).mapped('balance'))),
            'amount_residual': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == self.partner_id).mapped('amount_residual'))),
            'payment_state': d.payment_state,
            'invoice_date': d.invoice_date or d.date,
            'is_selected': d.id in self.selected_invoice_ids.ids,
            'manual_amount': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable' and l.partner_id == self.partner_id).mapped('amount_residual'))),
        }) for d in docs_inv]
        
        for m_line in manual_inv_lines:
            inv_vals.append((0, 0, {
                'manual_ref': m_line.manual_ref,
                'invoice_date': m_line.invoice_date,
                'amount_total': m_line.amount_total,
                'amount_residual': m_line.amount_residual,
                'manual_amount': m_line.manual_amount,
                'is_selected': m_line.is_selected,
            }))
        self.line_invoice_ids = inv_vals

        # Populate Bills
        bill_domain = domain_base + [
            '|',
            ('move_type', 'in', ('in_invoice', 'in_refund')),
            '&', ('move_type', '=', 'entry'), ('line_ids.account_id.account_type', '=', 'liability_payable')
        ]
        docs_bill = self.env['account.move'].search(bill_domain)
        
        # Filter zero residual entries
        docs_bill = docs_bill.filtered(lambda d: d.move_type != 'entry' or sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == self.partner_id).mapped('amount_residual')) > 0)

        manual_bill_lines = self.line_bill_ids.filtered(lambda l: not l.move_id)
        
        bill_vals = [(5, 0, 0)] + [(0, 0, {
            'move_id': d.id,
            'amount_total': abs(d.amount_total_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == self.partner_id).mapped('balance'))),
            'amount_residual': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == self.partner_id).mapped('amount_residual'))),
            'payment_state': d.payment_state,
            'invoice_date': d.invoice_date or d.date,
            'is_selected': d.id in self.selected_bill_ids.ids,
            'manual_amount': abs(d.amount_residual_signed) if d.move_type != 'entry' else abs(sum(d.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable' and l.partner_id == self.partner_id).mapped('amount_residual'))),
        }) for d in docs_bill]
        
        for m_line in manual_bill_lines:
            bill_vals.append((0, 0, {
                'manual_ref': m_line.manual_ref,
                'invoice_date': m_line.invoice_date,
                'amount_total': m_line.amount_total,
                'amount_residual': m_line.amount_residual,
                'manual_amount': m_line.manual_amount,
                'is_selected': m_line.is_selected,
            }))
        self.line_bill_ids = bill_vals

    def write(self, vals):
        """Override write to rebuild lines when filters change on saved records."""
        res = super().write(vals)
        if 'partner_id' in vals and self.state == 'draft':
            self._rebuild_lines()
        return res

    def action_refresh(self):
        """Manual refresh button to force-reload the lists."""
        self._rebuild_lines()
        return True

    def action_match_records(self):
        """Creates the netting move and reconciles it."""
        self.ensure_one()
        if self.state == 'matched':
            raise UserError(_("This record is already matched."))

        selected_invoices = self.line_invoice_ids.filtered(lambda l: l.is_selected)
        selected_bills = self.line_bill_ids.filtered(lambda l: l.is_selected)

        if not selected_invoices or not selected_bills:
            raise UserError(_("Please select at least one Invoice and one Bill to match."))

        total_inv = sum(line.manual_amount for line in selected_invoices)
        total_bill = sum(line.manual_amount for line in selected_bills)
        
        # Calculate max netting amount correctly
        net_amount = min(abs(total_inv), abs(total_bill))

        if net_amount <= 0:
            raise UserError(_("Matched amount must be greater than 0. Check your selection."))

        if net_amount <= 0:
            raise UserError(_("Matched amount must be greater than 0. Check your selection."))

        # Search for the "Nutting" journal specifically (Short Code 'NT')
        journal = self.env['account.journal'].search([('code', '=', 'NT')], limit=1)
        if not journal:
            # Fallback to any general journal if 'NT' not found
            journal = self.env['account.journal'].search([('type', '=', 'general')], limit=1)
            
        if not journal:
            raise UserError(_("Please create a Miscellaneous Journal first (ideally with Short Code 'NT')."))

        # Identify accounts
        first_inv_move = selected_invoices.filtered(lambda l: l.move_id).mapped('move_id')[:1]
        ar_account = first_inv_move.line_ids.filtered(lambda l: l.account_id.account_type == 'asset_receivable')[:1].account_id
        if not ar_account:
            ar_account = self.partner_id.property_account_receivable_id
            
        first_bill_move = selected_bills.filtered(lambda l: l.move_id).mapped('move_id')[:1]
        ap_account = first_bill_move.line_ids.filtered(lambda l: l.account_id.account_type == 'liability_payable')[:1].account_id
        if not ap_account:
            ap_account = self.partner_id.property_account_payable_id

        if not ar_account or not ap_account:
            raise UserError(_("Could not identify Receivable/Payable accounts."))

        # Identify currency
        currency = first_inv_move.currency_id or first_bill_move.currency_id or self.currency_id
        
        inv_names = [l.move_id.name or l.manual_ref for l in selected_invoices if l.move_id.name or l.manual_ref]
        bill_names = [l.move_id.name or l.manual_ref for l in selected_bills if l.move_id.name or l.manual_ref]
        
        inv_label = _('Netting AR: %s') % ", ".join(inv_names)
        bill_label = _('Netting AP: %s') % ", ".join(bill_names)

        # Create the netting journal entry
        move_vals = {
            'journal_id': journal.id,
            'date': self.date or fields.Date.context_today(self),
            'ref': _('Netting (%s): %s') % (self.name, self.partner_id.name),
            'line_ids': [
                (0, 0, {
                    'name': inv_label[:64], # Basic truncation for sanity
                    'partner_id': self.partner_id.id,
                    'account_id': ar_account.id,
                    'credit': net_amount,
                    'debit': 0.0,
                    'currency_id': currency.id,
                    'amount_currency': -net_amount,
                }),
                (0, 0, {
                    'name': bill_label[:64],
                    'partner_id': self.partner_id.id,
                    'account_id': ap_account.id,
                    'credit': 0.0,
                    'debit': net_amount,
                    'currency_id': currency.id,
                    'amount_currency': net_amount,
                }),
            ]
        }
        net_move = self.env['account.move'].create(move_vals)
        net_move.action_post()

        # Reconcile AR
        ar_net_line = net_move.line_ids.filtered(lambda l: l.account_id == ar_account)
        # Only reconcile real invoices (those with move_id)
        inv_lines = selected_invoices.filtered(lambda l: l.move_id).mapped('move_id.line_ids').filtered(
            lambda l: l.account_id == ar_account and not l.reconciled)
        
        if ar_net_line and inv_lines:
            try:
                (ar_net_line | inv_lines).reconcile()
            except Exception:
                # Fallback: try one by one if batch fails (unlikely but safe)
                for ln in inv_lines:
                    if not ln.reconciled:
                        try:
                            (ar_net_line | ln).reconcile()
                        except Exception:
                            pass

        # Reconcile AP
        ap_net_line = net_move.line_ids.filtered(lambda l: l.account_id == ap_account)
        # Only reconcile real bills (those with move_id)
        bill_lines = selected_bills.filtered(lambda l: l.move_id).mapped('move_id.line_ids').filtered(
            lambda l: l.account_id == ap_account and not l.reconciled)
        
        if ap_net_line and bill_lines:
            try:
                (ap_net_line | bill_lines).reconcile()
            except Exception:
                # Fallback: try one by one
                for ln in bill_lines:
                    if not ln.reconciled:
                        try:
                            (ap_net_line | ln).reconcile()
                        except Exception:
                            pass

        # Update state
        self.write({
            'state': 'matched',
            'netting_move_id': net_move.id
        })
        return True

class PartnerMatchingLine(models.Model):
    _name = 'partner.matching.line'
    _description = 'Partner Matching Line'

    matching_inv_id = fields.Many2one('partner.matching', ondelete='cascade')
    matching_bill_id = fields.Many2one('partner.matching', ondelete='cascade')
    
    move_id = fields.Many2one('account.move', string="Document", readonly=True)
    manual_ref = fields.Char(string="Manual Ref")
    is_selected = fields.Boolean(string="Select")
    
    # Display fields
    invoice_date = fields.Date(string="Date")
    amount_total = fields.Float(string="Total", digits=(16, 3))
    amount_residual = fields.Float(string="Due Amount", digits=(16, 3))
    payment_state = fields.Selection([
        ('not_paid', 'Not Paid'),
        ('partial', 'Partially Paid'),
        ('paid', 'Paid'),
        ('in_payment', 'In Payment'),
        ('reversed', 'Reversed'),
        ('blocked', 'Blocked')
    ], string="Status", readonly=True)
    manual_amount = fields.Float(string="Matching Amount", digits=(16, 3))

    @api.constrains('manual_amount')
    def _check_manual_amount(self):
        for line in self:
            if line.move_id and abs(line.manual_amount) > abs(line.amount_residual):
                raise UserError(_("Matching amount (%s) cannot exceed the due amount (%s) for %s.") % (
                    line.manual_amount, line.amount_residual, line.move_id.name))


    @api.onchange('is_selected')
    def _onchange_is_selected(self):
        """Updates the parent selection store when a box is checked."""
        if self.matching_inv_id:
            parent = self.matching_inv_id
            if self.is_selected:
                parent.selected_invoice_ids += self.move_id
            else:
                parent.selected_invoice_ids -= self.move_id
        if self.matching_bill_id:
            parent = self.matching_bill_id
            if self.is_selected:
                parent.selected_bill_ids += self.move_id
            else:
                parent.selected_bill_ids -= self.move_id


import math
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.biotex_base.models.integrity import convert_qty, guard_write, lock_records, require_group, transition


class Mask(models.Model):
    _inherit = 'biotex.remision.mask'

    application_state = fields.Selection([
        ('draft', 'Borrador'), ('support', 'Pendiente de soporte'), ('validated', 'Validada'),
        ('applied', 'Aplicada'), ('review', 'En revisión'), ('reversed', 'Revertida'), ('legacy', 'Antecedente por conciliar'),
    ], default='draft', required=True, copy=False, index=True)
    contract_id = fields.Many2one('biotex.contract', related='contract_line_id.contract_id', store=True)
    company_id = fields.Many2one(related='remision_id.company_id', store=True)
    correspondence = fields.Selection([('identity', 'Mismo producto / alias'), ('conversion', 'Presentación convertible'),
                                        ('substitution', 'Sustitución documentada')], default='identity', required=True)
    product_qty = fields.Float(string='Cantidad contractual demostrable', default=0)
    declared_amount = fields.Monetary(string='Importe administrativo (sustitución)')
    source_qty = fields.Float(string='Cantidad física demostrable', compute='_compute_source_qty', store=True)
    source_amount = fields.Monetary(string='Porción de valor real', required=True)
    amount_difference = fields.Monetary(string='Diferencia explicada', compute='_compute_difference')
    reason = fields.Text(string='Motivo / explicación')
    evidence_ids = fields.Many2many('ir.attachment', string='Sustento de la correspondencia', copy=False)
    exception_id = fields.Many2one('biotex.exception', string='Autorización de diferencia', ondelete='restrict')
    invoice_line_id = fields.Many2one('account.move.line', string='Renglón del documento económico', ondelete='restrict', index=True, copy=False)
    applied_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    applied_on = fields.Datetime(readonly=True, copy=False)
    reversed_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    reversed_on = fields.Datetime(readonly=True, copy=False)
    reversal_reason = fields.Text(string='Motivo del reverso', copy=False)
    legacy_amount = fields.Monetary(string='Consumo histórico reservado', readonly=True, copy=False)

    @api.depends('correspondence', 'product_qty', 'contract_line_id.price_unit', 'declared_amount')
    def _compute_amount(self):
        for mask in self:
            mask.amount = (mask.declared_amount if mask.correspondence == 'substitution'
                           else mask.product_qty * mask.contract_line_id.price_unit)

    @api.depends('product_qty', 'correspondence', 'line_id.product_uom_id', 'contract_line_id.uom_id')
    def _compute_source_qty(self):
        for mask in self:
            mask.source_qty = (convert_qty(mask.product_qty, mask.contract_line_id.uom_id, mask.line_id.product_uom_id)
                               if mask.correspondence != 'substitution' and mask.line_id and mask.contract_line_id.uom_id else 0)

    @api.depends('amount', 'source_amount')
    def _compute_difference(self):
        for mask in self:
            mask.amount_difference = mask.amount - mask.source_amount

    @api.constrains('product_qty', 'declared_amount', 'source_amount', 'correspondence')
    def _check_qty(self):
        for mask in self:
            if not all(math.isfinite(v) and v >= 0 for v in (mask.product_qty, mask.declared_amount, mask.source_amount)):
                raise ValidationError('Las porciones deben ser finitas y no negativas.')
            if mask.correspondence == 'substitution' and mask.product_qty:
                raise ValidationError('Una sustitución se aplica por importe; no invente unidades de otro artículo.')

    @api.model_create_multi
    def create(self, vals_list):
        if any(v.get('application_state', 'draft') != 'draft' or v.get('applied_by_id') or v.get('applied_on') for v in vals_list):
            raise UserError('Las aplicaciones nuevas comienzan en borrador.')
        parents = self.env['biotex.remision'].browse([v['remision_id'] for v in vals_list if v.get('remision_id')])
        lock_records(parents)
        return super().create(vals_list)

    def write(self, vals):
        guard_write(self, vals, ('application_state', 'applied_by_id', 'applied_on', 'reversed_by_id', 'reversed_on', 'legacy_amount'))
        if set(vals) & {'line_id', 'remision_id', 'contract_line_id', 'product_qty', 'declared_amount', 'source_amount',
                        'invoice_line_id', 'correspondence', 'reason', 'exception_id', 'evidence_ids'}:
            lock_records(self.remision_id)
            if any(m.application_state not in ('draft', 'support', 'legacy') for m in self):
                raise UserError('Conserve la aplicación confirmada; revierta el vínculo antes de reasignar.')
        return super().write(vals)

    def unlink(self):
        if any(m.application_state not in ('draft', 'support') for m in self):
            raise UserError('Conserve los vínculos históricos y sus reversos.')
        return super().unlink()

    def _check_eligibility(self):
        self.ensure_one()
        if not self.line_id or self.line_id.remision_id != self.remision_id:
            raise UserError('Identifique el renglón real de origen en esta remisión.')
        contract = self.contract_id
        if contract.company_id != self.company_id or contract.partner_id.commercial_partner_id != self.remision_id.partner_id.commercial_partner_id:
            raise UserError('Empresa y deudor deben coincidir con la operación original.')
        if contract.currency_id != self.currency_id or contract.tax_basis != self.remision_id.tax_basis:
            raise UserError('Compare la misma moneda y base de impuestos.')
        if contract.hospital_ids and self.remision_id.partner_shipping_id not in contract.hospital_ids:
            raise UserError('El destino no está autorizado en este contrato.')
        if self.amount <= 0 or self.source_amount <= 0:
            raise UserError('Indique importes positivos a cada lado de la aplicación.')
        if self.correspondence == 'substitution':
            if not self.reason or not self.evidence_ids or not self.exception_id:
                raise UserError('La sustitución requiere explicación, soporte original y autorización.')
            self.exception_id._check_valid(self.company_id, 'substitution')
        else:
            if self.line_id.product_id != self.contract_line_id.product_id:
                raise UserError('Un alias requiere el mismo producto interno; una similitud es una sustitución.')
            convert_qty(self.product_qty, self.contract_line_id.uom_id, self.line_id.product_uom_id)
        if self.currency_id.compare_amounts(self.source_amount, self.amount) and (not self.reason or not self.evidence_ids):
            raise UserError('Explique y respalde la diferencia de valor, sin cambiar la remisión.')
        if self.invoice_line_id:
            invoice = self.invoice_line_id.move_id
            if (invoice.state != 'posted' or invoice.move_type != 'out_invoice' or invoice.company_id != self.company_id
                    or invoice.commercial_partner_id != self.remision_id.partner_id.commercial_partner_id
                    or invoice.currency_id != self.currency_id):
                raise UserError('El documento económico debe estar vigente y corresponder a las mismas partes y moneda.')

    def action_validate(self):
        require_group(self, 'biotex_base.group_biotex_coordinator')
        lock_records(self.remision_id)
        for mask in self:
            if mask.application_state == 'validated':
                continue
            if mask.application_state not in ('draft', 'support', 'legacy'):
                raise UserError('Solo se validan aplicaciones pendientes.')
            mask._check_eligibility()
            transition(mask, {'application_state': 'validated'})

    def action_apply(self):
        require_group(self, 'biotex_base.group_biotex_coordinator')
        lock_records(self.contract_id)
        lock_records(self.remision_id)
        lock_records(self.invoice_line_id.move_id)
        lock_records(self)
        for mask in self:
            if mask.application_state == 'applied':
                continue
            if mask.application_state != 'validated':
                raise UserError('Valide soporte y correspondencia antes de aplicar.')
            mask._check_eligibility()
            if mask.remision_id.picking_id.state != 'done':
                raise UserError('La entrega real debe estar confirmada; el documento anticipado conserva el pendiente físico.')
            mask.contract_id.check_can_consume(mask.amount - mask.legacy_amount)
            siblings = self.search([('line_id', '=', mask.line_id.id), ('id', '!=', mask.id), ('application_state', 'in', ('applied', 'review', 'legacy'))])
            if mask.currency_id.compare_amounts(sum(siblings.mapped('source_amount')) + mask.source_amount, mask.line_id.amount_net) > 0:
                raise UserError('La porción aplicada excede la entrega neta disponible.')
            if mask.correspondence != 'substitution' and mask.line_id.product_uom_id.compare(sum(siblings.mapped('source_qty')) + mask.source_qty, mask.line_id.qty_net) > 0:
                raise UserError('La cantidad ya fue aplicada o devuelta.')
            if mask.invoice_line_id:
                other = self.search([('invoice_line_id', '=', mask.invoice_line_id.id), ('application_state', 'in', ('applied', 'review'))])
                value = mask.invoice_line_id.price_total if mask.remision_id.tax_basis == 'total' else mask.invoice_line_id.price_subtotal
                if mask.currency_id.compare_amounts(sum(other.mapped('amount')) + mask.amount, value) > 0:
                    raise UserError('La aplicación excede el saldo del renglón económico.')
            transition(mask, {'application_state': 'applied', 'applied_by_id': self.env.uid, 'applied_on': fields.Datetime.now()})
        return True

    def action_reverse(self):
        require_group(self, 'biotex_base.group_biotex_coordinator')
        lock_records(self.contract_id)
        lock_records(self.remision_id)
        lock_records(self)
        for mask in self:
            if mask.application_state == 'reversed':
                continue
            if mask.application_state not in ('applied', 'review') or not mask.reversal_reason:
                raise UserError('Registre el motivo del reverso de una aplicación vigente.')
            if mask.invoice_line_id and mask.invoice_line_id.move_id.state == 'posted':
                raise UserError('La aplicación respalda un documento vigente: revise primero la corrección económica relacionada.')
            transition(mask, {'application_state': 'reversed', 'reversed_by_id': self.env.uid, 'reversed_on': fields.Datetime.now()})
        return True

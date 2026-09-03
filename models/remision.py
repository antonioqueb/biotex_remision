from collections import defaultdict

from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError


class BiotexRemision(models.Model):
    """Remisión: documento de entrega firmado por la institución; base del cobro (R30, R31)."""
    _name = 'biotex.remision'
    _description = 'Remisión'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Folio', default='Nuevo', readonly=True, copy=False, index=True)
    state = fields.Selection([
        ('draft', 'Borrador'),
        ('confirmed', 'Confirmada'),
        ('delivered', 'Entregada'),
        ('signed', 'Firmada'),
        ('invoiced', 'Facturada'),
        ('cancelled', 'Cancelada'),
    ], default='draft', tracking=True, index=True, copy=False)
    date = fields.Date(default=fields.Date.context_today, required=True, tracking=True)
    support_type = fields.Selection([
        ('contract', 'Contrato'), ('emergent', 'Compra directa / emergente'), ('private', 'Cliente privado')],
        required=True, default='contract', tracking=True)
    contract_id = fields.Many2one('biotex.contract', string='Contrato', tracking=True, domain=[('state', '=', 'active')])
    partner_id = fields.Many2one(
        'res.partner', string='Institución / cliente', required=True, tracking=True,
        compute='_compute_from_contract', store=True, readonly=False, precompute=True)
    partner_shipping_id = fields.Many2one('res.partner', string='Lugar de entrega (unidad / hospital)')
    company_id = fields.Many2one(
        'res.company', string='Razón social que emite', required=True, tracking=True,
        compute='_compute_from_contract', store=True, readonly=False, precompute=True,
        default=lambda self: self.env.company,
        help='Logo y datos fiscales de la remisión. Viene del contrato (R30).')
    currency_id = fields.Many2one(related='company_id.currency_id')
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Delegación que surte', required=True, tracking=True,
        default=lambda self: self.env.user.biotex_default_warehouse_id,
        domain=[('biotex_is_delegation', '=', True)])
    stock_company_id = fields.Many2one(related='warehouse_id.company_id', string='Razón social dueña del inventario')
    is_intercompany = fields.Boolean(compute='_compute_is_intercompany', store=True,
                                     help='La entrega la hace una razón social distinta a la que emite (dropshipping interno).')
    direct_delivery = fields.Boolean(
        string='Entrega directa proveedor → institución',
        help='No toca almacén: se registra entrada y salida en la misma operación.')
    user_id = fields.Many2one('res.users', string='Responsable', default=lambda self: self.env.user, tracking=True)
    support_ref = fields.Char(string='Oficio / pedido / referencia')
    line_ids = fields.One2many('biotex.remision.line', 'remision_id', string='Productos entregados', copy=True)
    mask_ids = fields.One2many('biotex.remision.mask', 'remision_id', string='Claves a cobrar (máscara)', copy=True)
    picking_id = fields.Many2one('stock.picking', string='Salida de almacén', readonly=True, copy=False)
    invoice_id = fields.Many2one('account.move', string='Factura', readonly=True, copy=False)
    amount_delivered = fields.Monetary(string='Valor entregado (ref.)', compute='_compute_amounts', store=True)
    amount_billed = fields.Monetary(string='Importe a cobrar', compute='_compute_amounts', store=True)
    amount_diff = fields.Monetary(string='Diferencia', compute='_compute_amounts', store=True)
    mask_balance = fields.Json(compute='_compute_mask_balance')
    signature = fields.Binary(string='Firma', copy=False, attachment=True)
    signed_by = fields.Char(string='Recibió (nombre y cargo)', copy=False)
    signed_on = fields.Datetime(copy=False)
    notes = fields.Text(string='Observaciones')
    cancel_reason = fields.Char(readonly=True, copy=False)

    # ------------------------------------------------------------ computes
    @api.depends('contract_id')
    def _compute_from_contract(self):
        for r in self:
            if r.contract_id:
                r.partner_id = r.contract_id.partner_id
                r.company_id = r.contract_id.company_id
            else:
                r.partner_id = r.partner_id
                r.company_id = r.company_id or self.env.company

    @api.depends('company_id', 'warehouse_id.company_id')
    def _compute_is_intercompany(self):
        for r in self:
            r.is_intercompany = bool(r.warehouse_id) and r.warehouse_id.company_id != r.company_id

    @api.depends('line_ids.amount', 'mask_ids.amount')
    def _compute_amounts(self):
        for r in self:
            r.amount_delivered = sum(r.line_ids.mapped('amount'))
            r.amount_billed = sum(r.mask_ids.mapped('amount'))
            r.amount_diff = r.amount_billed - r.amount_delivered

    def _compute_mask_balance(self):
        for r in self:
            contract = r.contract_id
            r.mask_balance = {
                'currency': r.currency_id.symbol,
                'delivered': r.amount_delivered, 'billed': r.amount_billed, 'diff': r.amount_diff,
                'has_contract': bool(contract),
                'contract_total': contract.amount_total, 'contract_delivered': contract.amount_delivered,
                'contract_remaining': contract.amount_remaining,
                'after_remaining': contract.amount_remaining - (r.amount_billed if r.state in ('draft', 'confirmed') else 0.0),
                'tolerance_pct': contract.tolerance_pct,
                'lines': [{'product': l.product_id.display_name, 'qty': l.product_qty, 'amount': l.amount,
                           'mapped': sum(l.mask_ids.mapped('amount'))} for l in r.line_ids],
                'masks': [{'code': m.contract_line_id.code, 'name': m.contract_line_id.name, 'qty': m.product_qty,
                           'remaining': m.contract_line_id.qty_remaining, 'amount': m.amount} for m in r.mask_ids],
            }

    # ------------------------------------------------------------ constraints
    @api.constrains('support_type', 'contract_id')
    def _check_support(self):
        for r in self:
            if r.support_type == 'contract' and not r.contract_id:
                raise ValidationError('Indique el contrato de la remisión.')

    @api.constrains('mask_ids', 'contract_id')
    def _check_masks(self):
        for r in self:
            bad = r.mask_ids.filtered(lambda m: m.contract_line_id.contract_id != r.contract_id)
            if bad:
                raise ValidationError('Las claves a cobrar deben pertenecer al contrato %s.' % r.contract_id.name)

    # ------------------------------------------------------------ crud
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'Nuevo') == 'Nuevo':
                vals['name'] = self.env['ir.sequence'].next_by_code('biotex.remision') or 'Nuevo'
        return super().create(vals_list)

    def unlink(self):
        if any(r.state != 'draft' for r in self):
            raise UserError('Solo se eliminan remisiones en borrador.')
        return super().unlink()

    # ------------------------------------------------------------ flujo
    def action_confirm(self):
        for r in self:
            if not r.line_ids:
                raise UserError('Capture los productos entregados.')
            if r.support_type == 'contract':
                if not r.mask_ids:
                    raise UserError('Asocie lo entregado a la(s) clave(s) del contrato con las que se cobrará (máscara).')
                r.contract_id.check_can_consume(r.amount_billed)
            r._create_picking()
            r._biotex_after_confirm()
            r.state = 'confirmed'
        return True

    def _biotex_after_confirm(self):
        """Gancho para multiempresa (venta interna sin traspaso físico)."""
        return True

    def action_deliver(self):
        for r in self:
            if r.picking_id and r.picking_id.state not in ('done', 'cancel'):
                picking = r.picking_id.sudo()
                picking.action_assign()
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                    move.picked = True
                picking.with_context(skip_backorder=True, skip_sms=True).button_validate()
            r.state = 'delivered'
            r.contract_id.line_ids._compute_delivered()
            r.contract_id._check_alerts()
        return True

    def action_sign(self):
        for r in self:
            if not r.signature or not r.signed_by:
                raise UserError('Capture la firma y el nombre de quien recibe.')
            r.write({'state': 'signed', 'signed_on': fields.Datetime.now()})
        return True

    def action_cancel(self):
        for r in self:
            if r.invoice_id and r.invoice_id.state == 'posted':
                raise UserError('La remisión ya está facturada.')
            if r.picking_id and r.picking_id.state == 'done':
                raise UserError('La salida de almacén ya fue validada. Genere una devolución desde el traslado.')
            r.picking_id.sudo().action_cancel()
            r._biotex_on_cancel()
            r.write({'state': 'cancelled'})
            r.contract_id.line_ids._compute_delivered()
        return True

    def _biotex_on_cancel(self):
        return True

    def action_draft(self):
        self.write({'state': 'draft'})

    def action_print(self):
        return self.env.ref('biotex_remision.action_report_remision').report_action(self)

    # ------------------------------------------------------------ inventario
    def _create_picking(self):
        self.ensure_one()
        wh = self.warehouse_id
        Picking = self.env['stock.picking'].sudo().with_company(wh.company_id)
        customer_loc = self.partner_id.property_stock_customer or self.env.ref('stock.stock_location_customers')
        uom_field = 'product_uom_id' if 'product_uom_id' in self.env['stock.move']._fields else 'product_uom'
        picking = Picking.create({
            'picking_type_id': wh.out_type_id.id,
            'location_id': wh.lot_stock_id.id,
            'location_dest_id': customer_loc.id,
            'partner_id': (self.partner_shipping_id or self.partner_id).id,
            'origin': self.name,
            'biotex_remision_id': self.id,
            'move_ids': [(0, 0, {
                'product_id': l.product_id.id,
                'product_uom_qty': l.product_qty,
                uom_field: l.product_uom_id.id,
                'location_id': wh.lot_stock_id.id,
                'location_dest_id': customer_loc.id,
            }) for l in self.line_ids],
        })
        picking.action_confirm()
        picking.action_assign()
        self.picking_id = picking
        return picking

    # ------------------------------------------------------------ facturación agrupada (R33)
    def action_create_invoice(self):
        """Agrupa remisiones firmadas/entregadas por (razón social, cliente, contrato) en una factura."""
        groups = defaultdict(lambda: self.env['biotex.remision'])
        for r in self:
            if r.state not in ('delivered', 'signed'):
                raise UserError('%s: solo se facturan remisiones entregadas o firmadas.' % r.name)
            if r.invoice_id:
                raise UserError('%s ya está facturada (%s).' % (r.name, r.invoice_id.name))
            groups[(r.company_id, r.partner_id, r.contract_id)] |= r
        invoices = self.env['account.move']
        generic = self.env.ref('biotex_remision.product_contract_key', raise_if_not_found=False)
        for (company, partner, contract), remisions in groups.items():
            lines = []
            for r in remisions:
                sources = r.mask_ids if r.mask_ids else r.line_ids
                for src in sources:
                    if src._name == 'biotex.remision.mask':
                        product = src.contract_line_id.product_id or generic
                        name = '%s - %s (Rem. %s)' % (src.contract_line_id.code, src.contract_line_id.name, r.name)
                        qty, price = src.product_qty, src.price_unit
                    else:
                        product = src.product_id
                        name = '%s (Rem. %s)' % (src.product_id.display_name, r.name)
                        qty, price = src.product_qty, src.price_unit
                    lines.append((0, 0, {
                        'product_id': product.id if product else False,
                        'name': name, 'quantity': qty, 'price_unit': price,
                        'tax_ids': [(6, 0, (product.taxes_id.filtered(lambda t: t.company_id == company)).ids)] if product else False,
                    }))
            invoice = self.env['account.move'].with_company(company).create({
                'move_type': 'out_invoice', 'partner_id': partner.id, 'company_id': company.id,
                'invoice_origin': ', '.join(remisions.mapped('name')),
                'ref': contract.name if contract else False,
                'invoice_line_ids': lines,
            })
            remisions.write({'invoice_id': invoice.id, 'state': 'invoiced'})
            invoices |= invoice
        self.mapped('contract_id.line_ids')._compute_delivered()
        return {'type': 'ir.actions.act_window', 'name': 'Facturas', 'res_model': 'account.move',
                'view_mode': 'list,form' if len(invoices) > 1 else 'form',
                'res_id': invoices.id if len(invoices) == 1 else False,
                'domain': [('id', 'in', invoices.ids)]}

    def action_auto_mask(self):
        """Propone máscara 1:1 cuando el producto entregado es el de la clave del contrato."""
        for r in self:
            if not r.contract_id:
                continue
            existing = r.mask_ids.mapped('line_id')
            vals = []
            for l in r.line_ids.filtered(lambda l: l not in existing):
                cl = r.contract_id.line_ids.filtered(lambda c: c.product_id == l.product_id and c.qty_remaining > 0)[:1]
                if cl:
                    vals.append((0, 0, {'line_id': l.id, 'contract_line_id': cl.id, 'product_qty': min(l.product_qty, cl.qty_remaining)}))
            if vals:
                r.mask_ids = vals
        return True


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    biotex_remision_id = fields.Many2one('biotex.remision', string='Remisión', index=True, copy=False)

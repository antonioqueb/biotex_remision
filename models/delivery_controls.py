import math
from odoo import api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.addons.biotex_base.models.integrity import convert_qty, guard_create, guard_write, lock_records, require_group, transition


class Remision(models.Model):
    _inherit = 'biotex.remision'

    support_type = fields.Selection(selection_add=[('regularization', 'Pendiente de regularización')], ondelete={'regularization': 'set default'})
    delegation_id = fields.Many2one('biotex.delegation', string='Delegación comercial', tracking=True)
    exception_id = fields.Many2one('biotex.exception', string='Autorización de regularización', ondelete='restrict')
    tax_basis = fields.Selection([('untaxed', 'Subtotal sin impuestos'), ('total', 'Total con impuestos')], default='untaxed', required=True)
    direct_picking_id = fields.Many2one('stock.picking', string='Entrega directa confirmada por el proveedor', ondelete='restrict')
    delivered_on = fields.Datetime(string='Fecha real de entrega', readonly=True, copy=False)
    delivered_by_id = fields.Many2one('res.users', readonly=True, copy=False)
    amount_net = fields.Monetary(string='Entrega física neta', compute='_compute_operational_amounts', store=True)
    amount_applied = fields.Monetary(string='Aplicado administrativamente', compute='_compute_operational_amounts', store=True)
    amount_documented = fields.Monetary(string='Cobertura documental', compute='_compute_operational_amounts', store=True)
    amount_to_document = fields.Monetary(string='Entrega por documentar', compute='_compute_operational_amounts', store=True)
    amount_to_regularize = fields.Monetary(string='Entrega por regularizar', compute='_compute_operational_amounts', store=True)
    invoice_ids = fields.Many2many('account.move', compute='_compute_invoice_ids', string='Documentos relacionados')
    cancel_reason = fields.Char(string='Motivo de cancelación', copy=False, readonly=False)

    @api.depends('line_ids.amount_net', 'mask_ids.application_state', 'mask_ids.source_amount', 'mask_ids.invoice_line_id.move_id.state')
    def _compute_operational_amounts(self):
        for rec in self:
            applied = rec.mask_ids.filtered(lambda m: m.application_state in ('applied', 'review'))
            documented = applied.filtered(lambda m: m.invoice_line_id.move_id.state == 'posted')
            rec.amount_net = sum(rec.line_ids.mapped('amount_net'))
            rec.amount_applied = sum(applied.mapped('source_amount'))
            rec.amount_documented = sum(documented.mapped('source_amount'))
            rec.amount_to_document = max(rec.amount_net - rec.amount_documented, 0)
            rec.amount_to_regularize = max(rec.amount_net - rec.amount_applied, 0)

    @api.depends('invoice_id', 'mask_ids.invoice_line_id')
    def _compute_invoice_ids(self):
        for rec in self:
            rec.invoice_ids = rec.invoice_id | rec.mask_ids.invoice_line_id.move_id

    @api.constrains('mask_ids', 'contract_id')
    def _check_masks(self):
        # The compatibility header is retained. Each application has its own contract.
        for rec in self:
            if any(m.line_id and m.line_id.remision_id != rec for m in rec.mask_ids):
                raise ValidationError('La porción física debe pertenecer a esta remisión.')

    @api.model_create_multi
    def create(self, vals_list):
        guard_create(vals_list, ('picking_id', 'invoice_id', 'delivered_on', 'delivered_by_id', 'signed_on'))
        return super().create(vals_list)

    def write(self, vals):
        guard_write(self, vals, ('state', 'picking_id', 'invoice_id', 'delivered_on', 'delivered_by_id', 'signed_on'),
                    ('line_ids', 'partner_id', 'partner_shipping_id', 'company_id', 'warehouse_id', 'date',
                     'contract_id', 'support_type', 'tax_basis', 'direct_delivery', 'direct_picking_id'))
        if set(vals) & {'signature', 'signed_by'} and any(r.signed_on for r in self):
            raise UserError('La aceptación registrada se conserva; agregue una aclaración documental.')
        return super().write(vals)

    def action_confirm(self):
        lock_records(self)
        for rec in self:
            if rec.state in ('confirmed', 'delivered', 'signed', 'invoiced'):
                continue
            if rec.state != 'draft' or not rec.line_ids or not rec.partner_shipping_id:
                raise UserError('Capture productos y destino real en una remisión en borrador.')
            if rec.support_type == 'regularization':
                if not rec.exception_id:
                    raise UserError('La entrega pendiente de regularización requiere autorización.')
                rec.exception_id._check_valid(rec.company_id, 'regularization')
            if rec.contract_id:
                if rec.contract_id.company_id != rec.company_id or rec.contract_id.partner_id != rec.partner_id:
                    raise UserError('Empresa y cliente deben coincidir con el contrato.')
                if rec.contract_id.hospital_ids and rec.partner_shipping_id not in rec.contract_id.hospital_ids:
                    raise UserError('El hospital no está autorizado por el contrato.')
            rec.line_ids._check_actual_products()
            rec.line_ids._snapshot()
            rec._create_picking()
            rec._biotex_after_confirm()
            transition(rec, {'state': 'confirmed'})
        return True

    def _create_picking(self):
        self.ensure_one()
        if self.picking_id:
            return self.picking_id
        if self.direct_delivery:
            picking = self.direct_picking_id
            lock_records(picking)
            if (not picking or picking.state != 'done' or picking.location_id.usage != 'supplier'
                    or picking.location_dest_id.usage != 'customer' or picking.partner_id != self.partner_shipping_id):
                raise UserError('Relacione la entrega real proveedor → destinatario, ya confirmada, sin inventar una estancia en almacén.')
            if self.search_count([('picking_id', '=', picking.id), ('id', '!=', self.id)]):
                raise UserError('La entrega directa ya está vinculada a una remisión.')
            for line in self.line_ids:
                moves = picking.move_ids.filtered(lambda m: m.product_id == line.product_id and not m.biotex_remision_line_id)
                if len(moves) != 1 or line.product_uom_id.compare(convert_qty(moves.quantity, moves.product_uom, line.product_uom_id), line.product_qty):
                    raise UserError('Concuerde cada renglón con el movimiento real del proveedor.')
                moves.biotex_remision_line_id = line
            transition(self, {'picking_id': picking.id})
            return picking
        warehouse = self.warehouse_id
        warehouse.check_access('read')
        if warehouse.company_id not in self.env.companies:
            raise UserError('Habilite la empresa custodio dentro de su ámbito autorizado.')
        customer = self.partner_shipping_id.property_stock_customer or self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].with_company(warehouse.company_id).create({
            'picking_type_id': warehouse.out_type_id.id, 'location_id': warehouse.lot_stock_id.id,
            'location_dest_id': customer.id, 'partner_id': self.partner_shipping_id.id,
            'origin': self.name, 'biotex_remision_id': self.id,
            'move_ids': [(0, 0, {'product_id': line.product_id.id, 'product_uom_qty': line.product_qty,
                                'product_uom': line.product_uom_id.id, 'location_id': warehouse.lot_stock_id.id,
                                'location_dest_id': customer.id, 'biotex_remision_line_id': line.id}) for line in self.line_ids],
        })
        picking.action_confirm()
        picking.action_assign()
        transition(self, {'picking_id': picking.id})
        return picking

    def action_deliver(self):
        require_group(self, 'stock.group_stock_user')
        lock_records(self)
        for rec in self:
            if rec.state in ('delivered', 'signed', 'invoiced'):
                continue
            if rec.state != 'confirmed' or not rec.picking_id:
                raise UserError('Confirme primero la preparación de la remisión.')
            if rec.picking_id.state != 'done':
                return {'type': 'ir.actions.act_window', 'res_model': 'stock.picking', 'view_mode': 'form', 'res_id': rec.picking_id.id}
            transition(rec, {'state': 'delivered', 'delivered_on': rec.picking_id.date_done,
                             'delivered_by_id': self.env.uid})
        return True

    def action_sign(self):
        lock_records(self)
        for rec in self:
            if rec.signed_on:
                continue
            if rec.state not in ('delivered', 'invoiced') or not rec.signature or not rec.signed_by:
                raise UserError('La aceptación requiere entrega física, firma real y receptor identificado.')
            transition(rec, {'state': 'signed', 'signed_on': fields.Datetime.now()})
        return True

    def action_cancel(self):
        lock_records(self)
        for rec in self:
            if rec.state == 'cancelled':
                continue
            if (rec.line_ids.move_ids.filtered(lambda m: m.state == 'done') or rec.invoice_ids
                    or rec.mask_ids.filtered(lambda m: m.application_state not in ('draft', 'support'))):
                raise UserError('Hay hechos físicos o documentos relacionados. Registre devoluciones o correcciones propias.')
            if not rec.cancel_reason:
                raise UserError('Indique el motivo de cancelación.')
            rec.picking_id.action_cancel()
            transition(rec, {'state': 'cancelled'})
        return True

    def action_draft(self):
        raise UserError('Conserve la remisión cancelada y sus folios. Una corrección debe tener su propio antecedente.')

    def action_create_invoice(self):
        require_group(self, 'biotex_base.group_biotex_accounting')
        lock_records(self)
        if any(r.mask_ids for r in self):
            raise UserError('Registre el documento económico original en Contabilidad y vincule sus renglones desde las aplicaciones. No se generan conceptos sustitutos por importe.')
        # Ordinary invoices keep actual products. Existing relation makes retries idempotent.
        existing = self.invoice_id
        pending = self.filtered(lambda r: not r.invoice_id)
        if not pending:
            return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'view_mode': 'list,form', 'domain': [('id', 'in', existing.ids)]}
        if any(r.state not in ('delivered', 'signed') for r in pending):
            raise UserError('La factura desde remisiones requiere una entrega confirmada.')
        for rec in pending:
            invoice = self.env['account.move'].with_company(rec.company_id).create({
                'move_type': 'out_invoice', 'partner_id': rec.partner_id.id, 'company_id': rec.company_id.id,
                'currency_id': rec.currency_id.id, 'invoice_origin': rec.name,
                'invoice_line_ids': [(0, 0, {'product_id': l.product_id.id, 'name': l.description_snapshot,
                                            'quantity': l.qty_net, 'product_uom_id': l.product_uom_id.id,
                                            'price_unit': l.price_unit, 'tax_ids': [(6, 0, l.tax_ids.ids)],
                                            'biotex_remision_line_id': l.id}) for l in rec.line_ids if l.qty_net > 0],
            })
            transition(rec, {'invoice_id': invoice.id})
            existing |= invoice
        return {'type': 'ir.actions.act_window', 'res_model': 'account.move', 'view_mode': 'list,form', 'domain': [('id', 'in', existing.ids)]}


class RemisionLine(models.Model):
    _inherit = 'biotex.remision.line'

    company_id = fields.Many2one(related='remision_id.company_id', store=True)
    move_ids = fields.One2many('stock.move', 'biotex_remision_line_id')
    invoice_line_ids = fields.One2many('account.move.line', 'biotex_remision_line_id')
    tax_ids = fields.Many2many('account.tax', string='Impuestos acordados')
    description_snapshot = fields.Char(string='Descripción al confirmar', readonly=True, copy=False)
    code_snapshot = fields.Char(string='Código al confirmar', readonly=True, copy=False)
    brand_snapshot = fields.Char(string='Marca al confirmar', readonly=True, copy=False)
    qty_net = fields.Float(string='Entrega neta', compute='_compute_net', store=True)
    amount_net = fields.Monetary(string='Valor neto entregado', compute='_compute_net', store=True)

    @api.depends('move_ids.state', 'move_ids.quantity', 'move_ids.returned_move_ids.state',
                 'move_ids.returned_move_ids.quantity', 'price_unit', 'product_uom_id')
    def _compute_net(self):
        for line in self:
            delivered = line.move_ids.filtered(lambda m: m.state == 'done' and m.location_dest_id.usage == 'customer' and not m.origin_returned_move_id)
            returned = delivered.returned_move_ids.filtered(lambda m: m.state == 'done')
            line.qty_net = sum(convert_qty(m.quantity, m.product_uom, line.product_uom_id) for m in delivered) - sum(convert_qty(m.quantity, m.product_uom, line.product_uom_id) for m in returned)
            line.amount_net = line.qty_net * line.price_unit

    @api.depends('mask_ids.source_qty', 'mask_ids.application_state')
    def _compute_masked(self):
        for line in self:
            line.masked_qty = sum(line.mask_ids.filtered(lambda m: m.application_state in ('applied', 'review')).mapped('source_qty'))

    @api.constrains('product_qty', 'price_unit', 'product_id', 'product_uom_id')
    def _check_actual_products(self):
        for line in self:
            if not math.isfinite(line.product_qty) or line.product_qty <= 0 or not math.isfinite(line.price_unit) or line.price_unit < 0:
                raise ValidationError('Registre cantidades positivas y valores finitos no negativos.')
            if line.product_uom_id:
                convert_qty(line.product_qty, line.product_uom_id, line.product_id.uom_id)
            if not line.product_id.active:
                raise ValidationError('El artículo está desactivado para nuevas entregas.')

    def _snapshot(self):
        for line in self:
            line.write({'description_snapshot': line.product_id.display_name,
                        'code_snapshot': line.product_id.default_code, 'brand_snapshot': line.product_id.biotex_brand_id.name})

    @api.model_create_multi
    def create(self, vals_list):
        parents = self.env['biotex.remision'].browse([v['remision_id'] for v in vals_list if v.get('remision_id')])
        lock_records(parents)
        if any(r.state != 'draft' for r in parents):
            raise UserError('No agregue mercancía a una remisión confirmada.')
        return super().create(vals_list)

    def write(self, vals):
        if set(vals) & {'remision_id', 'product_id', 'product_qty', 'product_uom_id', 'price_unit', 'lot_name', 'tax_ids', 'description_snapshot', 'code_snapshot', 'brand_snapshot'}:
            lock_records(self.remision_id)
            if any(r.state != 'draft' for r in self.remision_id):
                raise UserError('El contenido real confirmado se conserva; registre una devolución o corrección.')
        return super().write(vals)

    def unlink(self):
        lock_records(self.remision_id)
        if any(r.state != 'draft' for r in self.remision_id):
            raise UserError('No borre productos de una remisión confirmada.')
        return super().unlink()


class StockMove(models.Model):
    _inherit = 'stock.move'

    biotex_remision_line_id = fields.Many2one('biotex.remision.line', index=True, ondelete='restrict', copy=True)


class InvoiceLine(models.Model):
    _inherit = 'account.move.line'

    biotex_remision_line_id = fields.Many2one('biotex.remision.line', index=True, ondelete='restrict', copy=False)

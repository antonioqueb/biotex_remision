/** @odoo-module **/
import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { formatMonetary } from "@web/views/fields/formatters";

export class BiotexMaskBalance extends Component {
    static template = "biotex_remision.MaskBalance";
    static props = { ...standardFieldProps };

    get d() {
        return this.props.record.data[this.props.name] || { lines: [], masks: [] };
    }
    fmt(v) {
        return `${this.d.currency || ""} ${formatMonetary(v || 0, { digits: [16, 2] })}`;
    }
    get diffClass() {
        const diff = Math.abs(this.d.diff || 0);
        if (!this.d.delivered) return "text-muted";
        return diff / this.d.delivered > 0.05 ? "text-warning fw-bold" : "text-success";
    }
    get exceeds() {
        if (!this.d.has_contract || !this.d.contract_total) return false;
        const limit = this.d.contract_total * (1 + (this.d.tolerance_pct || 0) / 100);
        return this.d.contract_delivered + this.d.billed > limit + 0.005;
    }
    get afterPct() {
        if (!this.d.contract_total) return 0;
        return Math.min(100, ((this.d.contract_delivered + this.d.billed) / this.d.contract_total) * 100);
    }
    get beforePct() {
        if (!this.d.contract_total) return 0;
        return Math.min(100, (this.d.contract_delivered / this.d.contract_total) * 100);
    }
}

registry.category("fields").add("biotex_mask_balance", {
    component: BiotexMaskBalance,
    supportedTypes: ["json"],
});

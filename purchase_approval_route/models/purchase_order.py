# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError
from odoo.tools.safe_eval import safe_eval


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    po_order_approval_route = fields.Selection(related='company_id.po_order_approval_route',
                                               string="Use Approval Route", readonly=True)

    team_id = fields.Many2one(
        comodel_name="purchase.team", string="Purchase Team", domain="[('company_id', '=', company_id)]",
        readonly=True, states={'draft': [('readonly', False)], 'sent': [('readonly', False)]}, ondelete="restrict"
    )

    approver_ids = fields.One2many(
        comodel_name="purchase.order.approver", inverse_name="order_id", string="Approvers", readonly=True)

    current_approver = fields.Many2one(
        comodel_name="purchase.order.approver", string="Approver",
        compute="_compute_approver")

    next_approver = fields.Many2one(
        comodel_name="purchase.order.approver", string="Next Approver",
        compute="_compute_approver")

    is_current_approver = fields.Boolean(
        string="Is Current Approver", compute="_compute_approver"
    )

    lock_amount_total = fields.Boolean(
        string="Lock Amount Total", compute="_compute_approver"
    )

    amount_total = fields.Monetary(tracking=True)
    name_for_email = fields.Char()

    def _track_subtype(self, init_values):
        self.ensure_one()
        if 'amount_total' in init_values and self.amount_total != init_values.get('amount_total'):
            self._check_lock_amount_total()
        return super(PurchaseOrder, self)._track_subtype(init_values)

    def product_computation_table(self):
        order_table = ''
        order_table += '''
                <table border=1 width=100% style='margin-top: 10px;'>
                <tr>
                    <td width="5%"><center><b>Sr.</b></center></td>
                    <td width="20%"><center><b>Name</b></center></td>
                    <td width="35%"><center><b>Description</b></center></td>
                    <td width="12%"><center><b>Quantity</b></center></td>
                    <td width="13%"><center><b>Unit Price</b></center></td>
                    <td width="15%"><center><b>Total</b></center></td>
                </tr>

                '''
        i = 0
        for line in self.order_line:
            i += 1
            order_table += "<tr>" + '<td align="center">' + str(
                i) + '</td>' + '<td>' + line.product_id.name + '</td>' + '<td>' + line.name + '</td>' + '<td align="center">' + str(
                line.product_qty) + '</td>' + '<td align="right">' + str(
                line.price_unit) + '</td>' + '<td align="right">' + str(line.price_subtotal) + '</td>' + "</tr>"

        order_table += "<tr>" + '<td colspan="5" width="80%" align="right">' + '<b>Untaxed Amount</b>' + '</td>' + '<td width="20%" align="right">' + str(
            self.amount_untaxed) + '<span>' + ' ' + '</span>' + line.currency_id.symbol + '</td>' + "</tr>"
        order_table += "<tr>" + '<td colspan="5" width="80%" align="right">' + '<b>Taxes</b>' + '</td>' + '<td width="20%" align="right">' + str(
            self.amount_tax) + '<span>' + ' ' + '</span>' + line.currency_id.symbol + '</td>' + "</tr>"
        order_table += "<tr>" + '<td colspan="5" width="80%" align="right">' + '<b>Total</b>' + '</td>' + '<td width="20%" align="right">' + '<b>' + str(
            self.amount_total) + '<span>' + ' ' + '</span>' + line.currency_id.symbol + '</b>' + '</td>' + "</tr>"

        i += 1
        order_table += '''
                        </table>
                        '''

        return order_table

    def button_approve(self, force=False):
        for order in self:
            if not order.team_id:
                # Do default behaviour if PO Team is not set
                super(PurchaseOrder, order).button_approve(force)
            elif order.current_approver:
                if order.current_approver.user_id == self.env.user or self.env.is_superuser():
                    # If current user is current approver (or superuser) update state as "approved"
                    order.current_approver.state = 'approved'
                    order.message_post(body=_('PO approved by %s') % self.env.user.name)
                    # Check is there is another approver
                    if order.next_approver:
                        # Send request to approve is there is next approver
                        order.send_to_approve()
                    else:
                        # If there is not next approval, than assume that approval is finished and send notification
                        partner = order.user_id.partner_id if order.user_id else order.create_uid.partner_id
                        order.message_post_with_view(
                            'purchase_approval_route.order_approval',
                            subject=_('PO Approved: %s') % (order.name,),
                            composition_mode='mass_mail',
                            partner_ids=[(4, partner.id)],
                            auto_delete=True,
                            auto_delete_message=True,
                            parent_id=False,
                            subtype_id=self.env.ref('mail.mt_note').id)
                        # Do default behaviour to set state as "purchase" and update date_approve
                        return super(PurchaseOrder, order).button_approve(force)

    def button_confirm(self):
        for order in self:
            if order.team_id:
                if order.approver_ids:
                    for approver in order.approver_ids:
                        mail_template = self.env.ref('purchase_approval_route.purchase_product_email_template')
                        order.write({'name_for_email':approver.user_id.name})
                        mail_template.email_to = approver.user_id.login
                        mail_template.send_mail(self.id, force_send=True)
            if order.state not in ['draft', 'sent']:
                continue

            if not order.team_id:
                # Do default behaviour if PO Team is not set
                super(PurchaseOrder, order).button_confirm()
            else:
                # Generate approval route and send PO to approve
                order.generate_approval_route()
                if order.next_approver:
                    # If approval route is generated and there is next approver mark the order "to approve"
                    order.write({'state': 'to approve'})
                    # And send request to approve
                    order.send_to_approve()
                else:
                    # If there are not approvers, do default behaviour and move PO to the "Purchase Order" state
                    super(PurchaseOrder, order).button_approve()

            order._add_supplier_to_product()
            if order.partner_id not in order.message_partner_ids:
                order.message_subscribe([order.partner_id.id])
        return True

    def generate_approval_route(self):
        """
        Generate approval route for order
        :return:
        """
        for order in self:
            if not order.team_id:
                continue
            if order.approver_ids:
                # reset approval route
                order.approver_ids.unlink()
            for team_approver in order.team_id.approver_ids:

                custom_condition = order.compute_custom_condition(team_approver)
                if not custom_condition:
                    # Skip approver, if custom condition for the approver is set and the condition result is not True
                    continue

                min_amount = team_approver.company_currency_id._convert(
                    team_approver.min_amount,
                    order.currency_id,
                    order.company_id,
                    order.date_order or fields.Date.today())
                if min_amount > order.amount_total:
                    # Skip approver if Minimum Amount is greater than Total Amount
                    continue
                max_amount = team_approver.company_currency_id._convert(
                    team_approver.max_amount,
                    order.currency_id,
                    order.company_id,
                    order.date_order or fields.Date.today())
                if max_amount and max_amount < order.amount_total:
                    # Skip approver if Maximum Amount is set and less than Total Amount
                    continue

                # Add approver to the PO
                self.env['purchase.order.approver'].create({
                    'sequence': team_approver.sequence,
                    'team_id': team_approver.team_id.id,
                    'user_id': team_approver.user_id.id,
                    'role': team_approver.role,
                    'min_amount': team_approver.min_amount,
                    'max_amount': team_approver.max_amount,
                    'lock_amount_total': team_approver.lock_amount_total,
                    'order_id': order.id,
                    'team_approver_id': team_approver.id,
                })

    def compute_custom_condition(self, team_approver):
        self.ensure_one()
        localdict = {'PO': self, 'USER': self.env.user}
        if not team_approver.custom_condition_code:
            return True
        try:
            safe_eval(team_approver.custom_condition_code, localdict, mode='exec', nocopy=True)
            return bool(localdict['result'])
        except Exception as e:
            raise UserError(_('Wrong condition code defined for %s. Error: %s') % (team_approver.display_name, e))

    @api.depends('approver_ids.state', 'approver_ids.lock_amount_total')
    def _compute_approver(self):
        for order in self:
            if not order.team_id:
                order.next_approver = False
                order.current_approver = False
                order.is_current_approver = False
                order.lock_amount_total = False
                continue
            next_approvers = order.approver_ids.filtered(lambda a: a.state == "to approve")
            order.next_approver = next_approvers[0] if next_approvers else False

            current_approvers = order.approver_ids.filtered(lambda a: a.state == "pending")
            order.current_approver = current_approvers[0] if current_approvers else False

            order.is_current_approver = (order.current_approver and order.current_approver.user_id == self.env.user) \
                                        or self.env.is_superuser()

            order.lock_amount_total = len(
                order.approver_ids.filtered(lambda a: a.state == "approved" and a.lock_amount_total)) > 0

    def send_to_approve(self):
        for order in self:
            if order.state != 'to approve' and not order.team_id:
                continue

            main_error_msg = _("Unable to send approval request to next approver.")
            if order.current_approver:
                reason_msg = _("The order must be approved by %s") % order.current_approver.user_id.name
                raise UserError("%s %s" % (main_error_msg, reason_msg))

            if not order.next_approver:
                reason_msg = _("There are no approvers in the selected PO team.")
                raise UserError("%s %s" % (main_error_msg, reason_msg))
            # use sudo as purchase user cannot update purchase.order.approver
            order.sudo().next_approver.state = 'pending'
            # Now next approver became as current
            current_approver_partner = order.current_approver.user_id.partner_id
            if current_approver_partner not in order.message_partner_ids:
                order.message_subscribe([current_approver_partner.id])
            order.with_user(order.user_id).message_post_with_view(
                'purchase_approval_route.request_to_approve',
                subject=_('PO Approval: %s') % (order.name,),
                composition_mode='mass_mail',
                partner_ids=[(4, current_approver_partner.id)],
                auto_delete=True,
                auto_delete_message=True,
                parent_id=False,
                subtype_id=self.env.ref('mail.mt_note').id)

    def _check_lock_amount_total(self):
        msg = _('Sorry, you are not allowed to change Amount Total of PO. ')
        for order in self:
            if order.state in ('draft', 'sent'):
                continue
            if order.lock_amount_total:
                reason = _('It is locked after received approval. ')
                raise UserError(msg + "\n\n" + reason)
            if order.team_id.lock_amount_total:
                reason = _('It is locked after generated approval route. ')
                suggestion = _('To make changes, cancel and reset PO to draft. ')
                raise UserError(msg + "\n\n" + reason + "\n\n" + suggestion)

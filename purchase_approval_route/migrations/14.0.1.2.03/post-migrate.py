# coding: utf-8

from odoo import api, SUPERUSER_ID


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    order_approvers = env['purchase.order.approver'].search([])
    for order_approver in order_approvers:
        team_approver = order_approver.team_approver_id
        if not team_approver:
            continue
        order_approver.write({
            'sequence': team_approver.sequence,
            'team_id': team_approver.team_id.id,
            'user_id': team_approver.user_id.id,
            'role': team_approver.role,
            'min_amount': team_approver.min_amount,
            'max_amount': team_approver.max_amount,
            'lock_amount_total': team_approver.lock_amount_total,
        })

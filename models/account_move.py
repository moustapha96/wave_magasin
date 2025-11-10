
from odoo import models, fields, api
from odoo.exceptions import ValidationError, UserError
import logging
import requests
from datetime import datetime
import json

_logger = logging.getLogger(__name__)

WAVE_API_URL = "https://api.wave.com/v1/checkout/sessions"
SUCCESS_URL = "https://portail.toubasandaga.sn/wave-paiement?transaction={}"

class AccountMove(models.Model):
    _inherit = 'account.move'

    # Relations
    wave_transaction_ids = fields.One2many(
        'wave.transaction',
        'account_move_id',
        string='Transactions Wave'
    )

    # Champs calculés
    wave_transaction_count = fields.Integer(
        string='Nombre de transactions Wave',
        compute='_compute_wave_stats',
        store=False
    )

    wave_total_paid = fields.Float(
        string='Total payé via Wave',
        compute='_compute_wave_stats',
        store=False
    )

    wave_payment_status = fields.Selection([
        ('none', 'Aucun paiement'),
        ('partial', 'Paiement partiel'),
        ('full', 'Entièrement payé'),
        ('overpaid', 'Surpayé')
    ], string='Statut paiement Wave', compute='_compute_wave_stats', store=False)


    has_wave_config = fields.Boolean(
        string='Configuration Wave disponible',
        compute='_compute_has_wave_config',
        store=False
    )

    @api.depends('wave_transaction_ids', 'wave_transaction_ids.status', 'wave_transaction_ids.amount')
    def _compute_wave_stats(self):
        """Calculer les statistiques des paiements Wave"""
        for facture in self:
            transactions = facture.wave_transaction_ids.filtered(lambda t: t.status == 'completed')
            facture.wave_transaction_count = len(facture.wave_transaction_ids)
            facture.wave_total_paid = sum(transactions.mapped('amount'))

            # Déterminer le statut de paiement
            if facture.wave_total_paid == 0:
                facture.wave_payment_status = 'none'
            elif facture.wave_total_paid < facture.amount_total:
                facture.wave_payment_status = 'partial'
            elif facture.wave_total_paid == facture.amount_total:
                facture.wave_payment_status = 'full'
            else:
                facture.wave_payment_status = 'overpaid'

    def _compute_has_wave_config(self):
        """Vérifier si une configuration Wave est disponible"""
        for facture in self:
            config = self.env['wave.config'].search([('is_active', '=', True)], limit=1)
            facture.has_wave_config = bool(config)

    def action_view_wave_transactions(self):
        """Action pour voir les transactions Wave de cette commande"""
        self.ensure_one()
        return {
            'name': f'Transactions Wave - {self.name}',
            'type': 'ir.actions.act_window',
            'view_mode': 'tree,form',
            'res_model': 'wave.transaction',
            'domain': [('account_move_id', '=', self.id)],
            'context': {
                'default_account_move_id': self.id,
                'default_partner_id': self.partner_id.id,
                'default_amount': self.amount_total,
                'default_currency': self.currency_id.name,
                'default_reference': self.name,
            },
            'target': 'current',
        }

    def action_initiate_wave_payment(self):
        """Action pour initier un paiement Wave"""
        try:
            # Récupérer les informations nécessaires
            transaction_id = f"TXN-{self.id}-{fields.Datetime.now().strftime('%Y%m%d%H%M%S')}"
            account_move_id = self.id
            partner_id = self.partner_id.id
            phone_number = self.partner_id.phone or ''
            amount = self.amount_total
            description = f"Paiement pour la facture {self.name}"
            currency = self.currency_id.name
            reference = self.name
            success_url = SUCCESS_URL.format(transaction_id)

            # Appeler la fonction pour initier le paiement Wave
            response = self._initiate_wave_payment(transaction_id, account_move_id, partner_id, phone_number, amount, description, currency, reference, success_url)

            if response.get('success'):
                payment_url = response.get('payment_url')
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Paiement Wave initié',
                        'message': f'Le paiement Wave a été initié avec succès. Le lien de paiement sera ouvert dans un nouvel onglet.',
                        'type': 'info',
                        'next': {
                            'type': 'ir.actions.act_url',
                            'url': payment_url,
                            'target': 'new',
                        }
                    }
                }
            else:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': 'Erreur',
                        'message': f'Erreur lors de l\'initiation du paiement Wave: {response.get("message")}',
                        'type': 'danger',
                    }
                }
        except Exception as e:
            _logger.error(f"Erreur lors de l'initiation du paiement Wave: {str(e)}")
            return {
                'type': 'ir.actions.client',
                'tag': 'display_notification',
                'params': {
                    'title': 'Erreur',
                    'message': f'Erreur lors de l\'initiation du paiement Wave: {str(e)}',
                    'type': 'danger',
                }
            }

    def _initiate_wave_payment(self, transaction_id, account_move_id, partner_id, phone_number, amount, description, currency, reference, success_url):
        """Initier un paiement Wave avec checkout sessions"""
        try:
            # Validation des paramètres requis
            data = {
                'transaction_id': transaction_id,
                'account_move_id': account_move_id,
                'partner_id': partner_id,
                'phoneNumber': phone_number,
                'amount': amount,
                'description': description,
                'currency': currency,
                'reference': reference,
                'success_url': success_url
            }

            # Récupérer la configuration Wave active
            config = self.env['wave.config'].sudo().search([('is_active', '=', True)], limit=1)
            if not config:
                return {'error': 'Wave configuration not found', 'success': False}

            # Vérifier l'existence de l'order et du partner
            account_move = self.env['account.move'].sudo().browse(int(account_move_id)) if account_move_id else None
            partner = self.env['res.partner'].sudo().browse(int(partner_id)) if partner_id else None

            if not account_move:
                return {'message': "La commande n'existe pas", 'success': False}
            if not partner:
                return {'message': "Le partenaire n'existe pas", 'success': False}

            # Vérifier si la transaction Wave existe déjà
            existing_tx = self.env['wave.transaction'].sudo().search([('transaction_id', '=', transaction_id)], limit=1)
            if existing_tx:
                return {
                    'success': True,
                    'transaction_id': existing_tx.transaction_id,
                    'wave_id': existing_tx.wave_id,
                    'session_id': existing_tx.wave_id,
                    'payment_url': existing_tx.payment_link_url,
                    'status': existing_tx.status or 'pending',
                    'account_move_id': existing_tx.account_move_id.id,
                    'partner_id': existing_tx.partner_id.id,
                    'reference': existing_tx.reference,
                    'success_url': success_url,
                    'existe': True
                }

            payload = {
                "amount": amount,
                "currency": currency,
                "success_url": success_url,
                "error_url": config.callback_url
            }

            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }

            # Appel à l'API Wave checkout sessions
            response = requests.post(
                WAVE_API_URL,
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in [200, 201]:
                data = response.json()
                _logger.info(f"Wave checkout sessions response: {data}")

                # Créer la transaction dans Odoo
                wave_transaction = self.env['wave.transaction'].sudo().create({
                    'wave_id': data.get('id'),
                    'transaction_id': transaction_id,
                    'amount': amount,
                    'currency': currency,
                    'status': 'pending',
                    'phone': phone_number,
                    'reference': reference,
                    'description': description,
                    'payment_link_url': data.get('wave_launch_url') or data.get('checkout_url'),
                    'wave_response': json.dumps(data),
                    'account_move_id': account_move.id,
                    'partner_id': partner.id,
                    'checkout_status': data.get('checkout_status'),
                    'payment_status': data.get('payment_status'),
                })

                return {
                    'success': True,
                    'transaction_id': wave_transaction.transaction_id,
                    'wave_id': data.get('id'),
                    'session_id': data.get('id'),
                    'payment_url': data.get('wave_launch_url') or data.get('checkout_url'),
                    'status': 'pending',
                    'account_move_id': wave_transaction.account_move_id.id,
                    'partner_id': wave_transaction.partner_id.id,
                    'reference': reference,
                    'checkout_status': data.get('checkout_status'),
                    'payment_status': data.get('payment_status'),
                }
            else:
                _logger.error(f"Wave API Error: {response.status_code} - {response.text}")
                return {'error': response.text, 'success': False}

        except Exception as e:
            _logger.error(f"Error initiating Wave payment: {str(e)}")
            return {'error': str(e), 'success': False}

    @api.model
    def get_invoice_details(self):
        """Renvoyer les détails du paiement"""
        if not self.payment_link:
            raise ValidationError("Aucun lien de paiement associé à cette facture.")

        line_items = []
        for line in self.invoice_line_ids:
            line_items.append({
                'id': line.id,
                'name': line.name,
                'quantity': line.quantity,
                'price_unit': line.price_unit,
                'price_subtotal': line.price_subtotal,
                'account': line.account_id.name
            })

        return {
            'id': self.id,
            'name': self.name,
            'state': self.state,
            'paid': self.payment_state,
            'transaction_id': self.transaction_id,
            'payment_link': self.payment_link,
            'partner_id': self.partner_id.id,
            'amount': self.amount_residual,
            'currency': self.currency_id.name,
            'invoice_date': self.invoice_date.isoformat() if self.invoice_date else None,
            'invoice_number': self.name,
            'line_items': line_items,
            'partner': {
                'id': self.partner_id.id,
                'name': self.partner_id.name,
                'email': self.partner_id.email,
                'phone': self.partner_id.phone,
                'mobile': self.partner_id.mobile,
                'address': self.partner_id.city
            }
        }

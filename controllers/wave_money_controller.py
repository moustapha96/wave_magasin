

from odoo import http, fields
from odoo.http import request, Response
import requests
import hmac
import hashlib
import json
import logging
import werkzeug
from datetime import datetime
import base64

_logger = logging.getLogger(__name__)

class WaveMoneyController(http.Controller):

    @http.route('/api/payment/wave/initiate', type='http', auth='public', cors='*', methods=['POST'], csrf=False)
    def initiate_wave_payment(self, **kwargs):
        """Initier un paiement Wave avec checkout sessions"""
        try:
            # Validation des paramètres requis
            data = json.loads(request.httprequest.data)
            transaction_id = data.get('transaction_id')
            partner_id = data.get('partner_id')
            phone_number = data.get('phoneNumber')
            amount = data.get('amount')
            description = data.get('description', 'Payment via Wave')
            currency = data.get('currency', 'XOF')
            reference = data.get('reference')
            success_url = data.get('success_url')
            facture_id = data.get('facture_id')

            # Validation des champs obligatoires
            if not all([transaction_id, facture_id, partner_id, phone_number, amount]):
                return self._make_response({'message': "Missing required fields: transaction_id, facture_id, partner_id"}, 400)

            # Récupérer la configuration Wave active
            config = request.env['wave.config'].sudo().search([('is_active', '=', True)], limit=1)
            if not config:
                return {'error': 'Wave configuration not found', 'success': False}

            # Vérifier l'existence de l'order et du partner
            account_move = request.env['account.move'].sudo().browse(int(facture_id)) if facture_id else None
            partner = request.env['res.partner'].sudo().browse(int(partner_id)) if partner_id else None

            if not account_move:
                return self._make_response({'message': "La commande n'existe pas"}, 400)
            if not partner:
                return self._make_response({'message': "Le partner n'existe pas"}, 400)

            # Vérifier si la transaction Wave existe déjà
            existing_tx = request.env['wave.transaction'].sudo().search([('transaction_id', '=', transaction_id)], limit=1)
            if existing_tx:
                return self._make_response({
                    'success': True,
                    'transaction_id': existing_tx.transaction_id,
                    'invoice': existing_tx.account_move_id.get_invoice_details(),
                    'wave_id': existing_tx.wave_id,
                    'session_id': existing_tx.wave_id,
                    'payment_url': existing_tx.payment_link_url,
                    'status': existing_tx.status or 'pending',
                    'account_move_id': existing_tx.account_move_id.id,
                    'partner_id': existing_tx.partner_id.id,
                    'reference': existing_tx.reference,
                    'success_url': success_url,
                    'existe': True
                }, 200)

            payload = {
                "amount": amount,
                "currency": currency,
                "success_url": f"https://www.ccbmshop.com/wave-paiement?transaction={transaction_id}",
                "error_url": config.callback_url
            }
            headers = {
                "Authorization": f"Bearer {config.api_key}",
                "Content-Type": "application/json",
            }

            # Appel à l'API Wave checkout sessions
            response = requests.post(
                "https://api.wave.com/v1/checkout/sessions",
                json=payload,
                headers=headers,
                timeout=30
            )

            if response.status_code in [200, 201]:
                data = response.json()
                _logger.info(f"Wave checkout sessions response: {data}")

                # Créer la transaction dans Odoo
                wave_transaction = request.env['wave.transaction'].sudo().create({
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

                return self._make_response({
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
                }, 200)

            else:
                _logger.error(f"Wave API Error: {response.status_code} - {response.text}")
                return self._make_response(response.text, 400)

        except Exception as e:
            _logger.error(f"Error initiating Wave payment: {str(e)}")
            return self._make_response(str(e), 400)

    @http.route('/api/payment/wave/status/<string:transaction_id>', type='http', auth='public', cors='*', methods=['GET'])
    def get_wave_payment_status_with_transaction_id(self, transaction_id, **kwargs):
        """Vérifier le statut d'un paiement Wave"""
        try:
            if not transaction_id:
                return Response(json.dumps({'error': 'Paiement wave avec cette transaction_id nexiste pas'}), status=400, mimetype='application/json')

            # Rechercher la transaction selon les paramètres fournis
            transaction = None
            # Priorité 1: transaction_id (notre ID personnalisé)
            if transaction_id:
                transaction = request.env['wave.transaction'].sudo().search([('transaction_id', '=', transaction_id)], limit=1)
                result = self._refresh_transaction_status(transaction)
                if result:
                    transaction_up = request.env['wave.transaction'].sudo().search([('transaction_id', '=', transaction_id)], limit=1)
                    return self._make_response({
                        'success': True,
                        'transaction_id': transaction_up.transaction_id,
                        'custom_transaction_id': transaction_up.transaction_id,
                        'wave_id': transaction_up.wave_id,
                        'session_id': transaction_up.wave_id,
                        'reference': transaction_up.reference,
                        'status': transaction_up.status,
                        'checkout_status': transaction_up.checkout_status,
                        'payment_status': transaction_up.payment_status,
                        'amount': transaction_up.amount,
                        'currency': transaction_up.currency,
                        'phone': transaction_up.phone,
                        'description': transaction_up.description,
                        'payment_url': transaction_up.payment_link_url,
                        'account_move_id': transaction_up.account_move_id.id if transaction_up.account_move_id else False,
                        'account_move': transaction_up.account_move_id.get_invoice_details() if transaction_up.account_move_id else False,
                        'partner_id': transaction_up.partner_id.id if transaction_up.partner_id else False,
                        'created_at': transaction_up.created_at.isoformat() if transaction_up.created_at else None,
                        'updated_at': transaction_up.updated_at.isoformat() if transaction_up.updated_at else None,
                        'completed_at': transaction_up.completed_at.isoformat() if transaction_up.completed_at else None
                    }, 200)

                else:
                    return self._make_response({
                        'success': True,
                        'transaction_id': transaction.transaction_id,
                        'wave_id': transaction.wave_id,
                        'session_id': transaction.wave_id,
                        'payment_url': transaction.payment_link_url,
                        'status': transaction.status or 'pending',
                        'account_move_id': transaction.account_move_id.id,
                        'account_move': transaction.account_move_id.get_invoice_details(),
                        'partner_id': transaction.partner_id.id,
                        'reference': transaction.reference,
                        'existe': True
                    }, 200)

            return self._make_response({"error": "Transaction not found"}, 400)

        except Exception as e:
            _logger.error(f"Error getting Wave payment status: {str(e)}")
            return self._make_response({"error": str(e)}, 400)

    def _map_wave_status_to_odoo(self, checkout_status, payment_status):
        """Mapper les statuts Wave vers les statuts Odoo"""
        checkout_status = checkout_status.lower()
        payment_status = payment_status.lower()
        if checkout_status == 'complete' and payment_status == 'succeeded':
            return 'completed'
        elif checkout_status == 'failed' or payment_status == 'failed':
            return 'failed'
        elif checkout_status == 'cancelled' or payment_status == 'cancelled':
            return 'cancelled'
        elif checkout_status == 'expired':
            return 'expired'
        else:
            return 'pending'

    def _refresh_transaction_status(self, transaction):
        """Rafraîchir le statut d'une transaction depuis l'API Wave"""
        try:
            _logger.info(f"Refreshing status for transaction {transaction.id}")
            config = request.env['wave.config'].sudo().search([('is_active', '=', True)], limit=1)
            if not config:
                return False

            # Utiliser la méthode du modèle pour récupérer la session
            session_data = config.get_session_by_id(transaction.wave_id)
            if session_data:
                wave_status = session_data.get('status', '').lower()
                checkout_status = session_data.get('checkout_status', '').lower()
                payment_status = session_data.get('payment_status', '').lower()

                new_status = self._map_wave_status_to_odoo(checkout_status, payment_status)
                if new_status != transaction.status:
                    _logger.info(f"Updating status of transaction {transaction.id} from {transaction.status} to {new_status}")
                    transaction.write({
                        'status': new_status,
                        'updated_at': fields.Datetime.now(),
                        'wave_response': json.dumps(session_data),
                        'checkout_status': session_data.get('checkout_status'),
                        'payment_status': session_data.get('payment_status'),
                        'completed_at': session_data.get('when_completed'),
                    })
                return True

        except Exception as e:
            _logger.error(f"Error refreshing transaction status: {str(e)}")
            return False

    def _make_response(self, data, status):
        return request.make_response(
            json.dumps(data),
            status=status,
            headers={'Content-Type': 'application/json'}
        )

    def convert_iso_format_to_custom_format(self, iso_date):
        try:
            # Parse the ISO format date
            dt = datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ")
            # Convert to the desired format
            custom_format_date = dt.strftime("%Y-%m-%d %H:%M:%S")
            return custom_format_date
        except ValueError as e:
            _logger.error(f"Error converting date format: {str(e)}")
            return None

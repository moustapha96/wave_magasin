

from odoo import http, fields
from odoo.http import request, Response
import logging
import json
from datetime import datetime

_logger = logging.getLogger(__name__)

class WaveMoneyWebhookController(http.Controller):

    def _map_wave_status_to_odoo(self, checkout_status, payment_status):
        status_map = {
            ('complete', 'succeeded'): 'completed',
            ('failed', 'any'): 'failed',
            ('any', 'failed'): 'failed',
            ('cancelled', 'any'): 'cancelled',
            ('any', 'cancelled'): 'cancelled',
            ('expired', 'any'): 'expired',
        }
        return status_map.get((checkout_status, payment_status), 'pending')

    @http.route('/wave/webhook', type='http', auth='public', csrf=False, methods=['POST'])
    def wave_webhook(self, **kwargs):
        try:
            config = request.env['wave.config'].sudo().search([('is_active', '=', True)], limit=1)
            if not config:
                return self._json_response({'error': 'Configuration not found'}, 400)

            body = request.httprequest.get_data()
            try:
                webhook_data = json.loads(body.decode('utf-8'))
                _logger.info(f"Received Wave webhook data: {webhook_data}")
            except json.JSONDecodeError:
                return self._json_response({'error': 'Invalid JSON'}, 400)

            result = self._process_wave_webhook(webhook_data)
            return self._json_response(result, 200)

        except Exception as e:
            _logger.exception("Webhook error: %s", str(e))
            return self._json_response({'error': 'Internal server error'}, 500)

    def _process_wave_webhook(self, webhook_data):
        event_type = webhook_data.get('type') or webhook_data.get('event')
        _logger.info(f"Processing Wave event: {event_type}")

        if event_type != "checkout.session.completed":
            return {'success': False, 'error': 'Unhandled event'}

        session = webhook_data.get('data', {})
        session_id = session.get('id')
        if not session_id:
            return {'success': False, 'error': 'Missing session ID'}

        transaction = request.env['wave.transaction'].sudo().search([('wave_id', '=', session_id)], limit=1)
        if not transaction:
            return {'success': False, 'error': 'Transaction not found'}

        checkout_status = session.get('checkout_status', '').lower()
        payment_status = session.get('payment_status', '').lower()
        new_status = self._map_wave_status_to_odoo(checkout_status, payment_status)

        transaction.write({
            'status': new_status,
            'updated_at': fields.Datetime.now(),
            'completed_at': self.convert_iso_format_to_custom_format(session.get('when_completed')),
            'webhook_data': json.dumps(webhook_data),
            'checkout_status': checkout_status,
            'payment_status': payment_status,
        })

        if new_status == 'completed':
            pourcentage = (100 * transaction.amount) / transaction.account_move_id.amount_total if transaction.account_move_id.amount_total else 0
            invoice = self.create_advance_invoice(transaction.account_move_id, pourcentage)
            if invoice:
                return self.process_payment(invoice, transaction.amount, request.env.company)
            else:
                return {'success': False, 'error': 'Invoice creation failed'}

        return {'success': True}

    def convert_iso_format_to_custom_format(self, iso_date):
        try:
            return datetime.strptime(iso_date, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    def _json_response(self, data, status):
        return Response(json.dumps(data), status=status, mimetype='application/json')

    def create_advance_invoice(self, account_move, percentage):
        """
        Crée une facture d'acompte en utilisant l'assistant Odoo
        Args:
            order: Objet sale.order
            percentage: Pourcentage de l'acompte
        Returns:
            account.move: Facture d'acompte créée
        """
        user = request.env['res.users'].sudo().browse(request.env.uid)
        if not user or user._is_public():
            admin_user = request.env.ref('base.user_admin')
            request.env = request.env(user=admin_user.id)
            _logger.info("Création de la facture d'acompte pour la commande %s avec pourcentage %.2f%% avec l'utilisateur administrateur par défaut", order.name, percentage)

        try:

            if account_move:
                if account_move.state == 'draft':
                    account_move.action_post()
                return account_move
            else:
                _logger.error("Impossible de créer la facture d'acompte pour la commande %s", account_move.name)
                return None

        except Exception as e:
            _logger.exception("Erreur lors de la création de la facture d'acompte: %s", str(e))
            return None

    def process_payment(self,invoice, amount, company):
        """
        Traite le paiement pour la facture d'acompte
        Args:
            order: Commande de vente
            invoice: Facture d'acompte
            amount: Montant du paiement
            company: Société
        Returns:
            dict: Résultat du traitement
        """
        user = request.env['res.users'].sudo().browse(request.env.uid)
        if not user or user._is_public():
            admin_user = request.env.ref('base.user_admin')
            request.env = request.env(user=admin_user.id)
            _logger.info("Traitement du paiement pour la facture d'acompte %s avec l'utilisateur administrateur par défaut", invoice.name)

        try:
            journal = request.env['account.journal'].sudo().search([
                ('code', '=', 'CSH1'),
                ('company_id', '=', company.id)
            ], limit=1)

            if not journal:
                journal = request.env['account.journal'].sudo().search([
                    ('type', 'in', ['cash', 'bank']),
                    ('company_id', '=', company.id)
                ], limit=1)

            _logger.info("Journal de vente %s", journal.name)
                
            if not journal:
                return {'success': False, 'error': 'Journal de vente non trouvé'}


            payment = self._register_payment( invoice, amount, journal.id)
            if not payment:
                return {'success': False, 'error': 'Erreur lors de l\'enregistrement du paiement'}

            self._reconcile_payment_with_invoice(payment, invoice)

            return {
                'success': True,
                'payment_id': payment.id,
                'invoice_id': invoice.id,
                'amount': amount,
                'message': 'Paiement d\'acompte enregistré avec succès'
            }

        except Exception as e:
            _logger.exception("Erreur lors du traitement du paiement: %s", str(e))
            return {'success': False, 'error': str(e)}

    def _register_payment(self, invoice, amount, journal_id, payment_method_line_id=None):
        """
        Enregistre un paiement sur la facture.

        Args:
            order: Commande de vente
            invoice: objet account.move
            amount: montant du paiement
            journal_id: ID du journal (ex: banque)

        Returns:
            account.payment
        """
        try:

            payment_method = request.env['account.payment.method'].sudo().search([('payment_type', '=', 'inbound')], limit=1)
            _logger.info("payment_method: %s", payment_method)
            
            if not payment_method:
                payment_method = request.env['account.payment.method'].sudo().search([('payment_type', '=', 'inbound')], limit=1)


            payment_method_line = request.env['account.payment.method.line'].sudo().search([('payment_method_id', '=', payment_method.id)], limit=1)
            if not payment_method_line:
                payment_method_line = self.create_payment_method_line(payment_method.id, journal_id)
                if payment_method_line:
                    _logger.info(f"Ligne de méthode de paiement créée avec l'ID : {payment_method_line.id}")
                else:
                    _logger.info("Échec de la création de la ligne de méthode de paiement.")



            _logger.info("payment_method_line: %s", payment_method_line)

            payment_obj = request.env['account.payment'].create({
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': invoice.partner_id.id,
                'amount': amount,
                'journal_id': journal_id,
                'payment_method_line_id': payment_method_line.id,
                'payment_method_id': payment_method.id,
                'date': fields.Date.today(),
                'ref': f"{invoice.name}",
                'is_reconciled': True,
                'move_id': invoice.id
            })
            payment_obj.action_post()
            return payment_obj
        except Exception as e:
            _logger.exception("Erreur lors de l'enregistrement du paiement : %s", str(e))
            return None


    def create_payment_method_line(self, payment_method_id, journal_id):
        """
        Crée une ligne de méthode de paiement pour un journal donné.

        Args:
            payment_method_id (int): ID de la méthode de paiement (account.payment.method)
            journal_id (int): ID du journal (account.journal)

        Returns:
            account.payment.method.line: Ligne de méthode de paiement créée
        """
        try:
            # Vérifier que la méthode de paiement et le journal existent
            payment_method = request.env['account.payment.method'].browse(payment_method_id)
            journal = request.env['account.journal'].browse(journal_id)

            if not payment_method or not journal:
                raise ValueError("La méthode de paiement ou le journal n'existe pas.")

            # Créer la ligne de méthode de paiement
            payment_method_line = request.env['account.payment.method.line'].create({
                'name': f"{payment_method.name} - {journal.name}",
                'payment_method_id': payment_method_id,
                'journal_id': journal_id,
                'sequence': 10,
            })

            return payment_method_line
        except Exception as e:
            _logger.exception("Erreur lors de la création de la ligne de méthode de paiement : %s", str(e))
            return None




    def _reconcile_payment_with_invoice(self, payment, invoice):
        """
        Réconcilie le paiement avec la facture

        Args:
            payment: Objet account.payment
            invoice: Objet account.move
        """
        try:
            invoice_lines = invoice.line_ids.filtered(
                lambda line: line.account_id.account_type == 'asset_receivable' and not line.reconciled
            )

            if not invoice_lines:
                invoice_lines = invoice.line_ids.filtered(
                    lambda line: line.account_id.internal_type == 'receivable' and not line.reconciled
                )

            payment_lines = payment.move_id.line_ids.filtered(
                lambda line: line.account_id.account_type == 'asset_receivable'
            )

            if not payment_lines:
                payment_lines = payment.move_id.line_ids.filtered(
                    lambda line: line.account_id.internal_type == 'receivable'
                )

            lines_to_reconcile = invoice_lines + payment_lines
            if lines_to_reconcile:
                lines_to_reconcile.reconcile()
                _logger.info("Paiement %s réconcilié avec facture d'acompte %s", payment.name, invoice.name)
            else:
                _logger.warning("Aucune ligne à réconcilier trouvée pour le paiement %s et la facture %s",
                        payment.name, invoice.name)

        except Exception as e:
            _logger.exception("Erreur lors de la réconciliation du paiement: %s", str(e))
            return None

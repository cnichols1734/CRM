# services/report_service.py
"""
Report Service - Query building and execution for the Reports module.
All queries are automatically org-scoped for multi-tenant security.
"""

from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import func, case, and_, or_, extract, distinct
from sqlalchemy.orm import joinedload
from flask_login import current_user

from models import (
    db, Contact, ContactGroup, contact_groups, Task, TaskType, TaskSubtype,
    Transaction, TransactionType, TransactionParticipant, TransactionDocument,
    DocumentSignature, Interaction, User
)
from services.tenant_service import org_query


class ReportService:
    """Service for building and executing report queries."""

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _apply_user_filter(self, query, model, user_id):
        """
        Apply user filter to query if user_id is provided.
        
        Args:
            query: SQLAlchemy query object
            model: The model being queried (to determine the correct field)
            user_id: User ID to filter by, or None for all org records
            
        Returns:
            Filtered query
        """
        if user_id is None:
            return query
        
        # Different models have different user relationship fields
        if model == Transaction:
            return query.filter(Transaction.created_by_id == user_id)
        elif model == Contact:
            return query.filter(Contact.user_id == user_id)
        elif model == Task:
            return query.filter(Task.assigned_to_id == user_id)
        elif model == Interaction:
            return query.filter(Interaction.user_id == user_id)
        
        return query

    # =========================================================================
    # TRANSACTION REPORTS
    # =========================================================================

    def get_pipeline_overview(self, date_range=None, user_id=None):
        """Get transaction pipeline grouped by status with counts and values."""
        query = org_query(Transaction).join(
            TransactionType, Transaction.transaction_type_id == TransactionType.id
        )
        query = self._apply_user_filter(query, Transaction, user_id)

        if date_range:
            query = self._apply_date_filter(query, Transaction.created_at, date_range)

        # Group by status
        status_order = ['preparing_to_list', 'active', 'under_contract', 'closed', 'cancelled']

        results = query.with_entities(
            Transaction.status,
            func.count(Transaction.id).label('count')
        ).group_by(Transaction.status).all()

        # Convert to ordered dict
        status_data = {s: 0 for s in status_order}
        for status, count in results:
            if status in status_data:
                status_data[status] = count

        # Get detailed list
        transactions = query.order_by(Transaction.status, Transaction.expected_close_date).all()

        # Fetch participants separately to avoid N+1
        tx_ids = [t.id for t in transactions]
        participants = TransactionParticipant.query.filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True,
            TransactionParticipant.role.in_(['seller', 'buyer'])
        ).all() if tx_ids else []

        participant_map = {}
        for p in participants:
            if p.transaction_id not in participant_map:
                participant_map[p.transaction_id] = p

        rows = []
        for tx in transactions:
            participant = participant_map.get(tx.id)
            rows.append({
                'id': tx.id,
                'street_address': tx.street_address,
                'city': tx.city or '',
                'status': tx.status,
                'transaction_type': tx.transaction_type.display_name if hasattr(tx, 'transaction_type') else '',
                'expected_close_date': tx.expected_close_date.strftime('%m/%d/%Y') if tx.expected_close_date else '',
                'client_name': participant.display_name if participant else ''
            })

        return {
            'chart_data': {
                'labels': list(status_data.keys()),
                'values': list(status_data.values())
            },
            'rows': rows,
            'totals': {'total': len(transactions)}
        }

    def get_deals_closing_soon(self, date_range='this_month', user_id=None):
        """Get deals expected to close within the date range."""
        today = date.today()

        if date_range == 'this_week':
            end_date = today + timedelta(days=(6 - today.weekday()))
            start_date = today
        elif date_range == 'this_month':
            start_date = today
            if today.month == 12:
                end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(today.year, today.month + 1, 1) - timedelta(days=1)
        elif date_range == 'this_quarter':
            quarter = (today.month - 1) // 3
            start_date = today
            end_month = (quarter + 1) * 3
            if end_month > 12:
                end_date = date(today.year + 1, 1, 1) - timedelta(days=1)
            else:
                end_date = date(today.year, end_month + 1, 1) - timedelta(days=1)
        else:
            start_date = today
            end_date = today + timedelta(days=30)

        query = org_query(Transaction).join(
            TransactionType, Transaction.transaction_type_id == TransactionType.id
        ).filter(
            Transaction.expected_close_date >= start_date,
            Transaction.expected_close_date <= end_date,
            Transaction.status.notin_(['closed', 'cancelled'])
        )
        query = self._apply_user_filter(query, Transaction, user_id)
        query = query.order_by(Transaction.expected_close_date)

        transactions = query.all()

        # Fetch participants
        tx_ids = [t.id for t in transactions]
        participants = TransactionParticipant.query.filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True,
            TransactionParticipant.role.in_(['seller', 'buyer'])
        ).all() if tx_ids else []

        participant_map = {p.transaction_id: p for p in participants}

        rows = []
        for tx in transactions:
            participant = participant_map.get(tx.id)
            days_to_close = (tx.expected_close_date - today).days if tx.expected_close_date else None
            rows.append({
                'id': tx.id,
                'street_address': tx.street_address,
                'city': tx.city or '',
                'client_name': participant.display_name if participant else '',
                'expected_close_date': tx.expected_close_date.strftime('%m/%d/%Y') if tx.expected_close_date else '',
                'status': tx.status,
                'days_to_close': days_to_close
            })

        return {
            'rows': rows,
            'totals': {'total': len(transactions)}
        }

    def get_stale_deals(self, days_threshold=14, user_id=None):
        """Get active deals with no updates in X days."""
        threshold_date = datetime.utcnow() - timedelta(days=days_threshold)

        query = org_query(Transaction).join(
            TransactionType, Transaction.transaction_type_id == TransactionType.id
        ).filter(
            Transaction.status.in_(['preparing_to_list', 'active', 'under_contract']),
            Transaction.updated_at < threshold_date
        )
        query = self._apply_user_filter(query, Transaction, user_id)
        query = query.order_by(Transaction.updated_at)

        transactions = query.all()

        # Fetch participants
        tx_ids = [t.id for t in transactions]
        participants = TransactionParticipant.query.filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True
        ).all() if tx_ids else []

        participant_map = {p.transaction_id: p for p in participants}

        rows = []
        now = datetime.utcnow()
        for tx in transactions:
            participant = participant_map.get(tx.id)
            days_since = (now - tx.updated_at).days if tx.updated_at else 0
            rows.append({
                'id': tx.id,
                'street_address': tx.street_address,
                'city': tx.city or '',
                'client_name': participant.display_name if participant else '',
                'status': tx.status,
                'days_since_update': days_since,
                'updated_at': tx.updated_at.strftime('%m/%d/%Y') if tx.updated_at else ''
            })

        return {
            'rows': rows,
            'totals': {'total': len(transactions)}
        }

    def get_transaction_type_distribution(self, date_range=None, user_id=None):
        """Get transaction counts by type."""
        query = org_query(Transaction).join(
            TransactionType, Transaction.transaction_type_id == TransactionType.id
        )
        query = self._apply_user_filter(query, Transaction, user_id)

        if date_range:
            query = self._apply_date_filter(query, Transaction.created_at, date_range)

        results = query.with_entities(
            TransactionType.display_name,
            func.count(Transaction.id).label('count')
        ).group_by(TransactionType.display_name).all()

        total = sum(r[1] for r in results)

        chart_data = {
            'labels': [r[0] for r in results],
            'values': [r[1] for r in results]
        }

        rows = []
        for name, count in results:
            percentage = round((count / total * 100), 1) if total > 0 else 0
            rows.append({
                'transaction_type': name,
                'count': count,
                'percentage': f'{percentage}%'
            })

        return {
            'chart_data': chart_data,
            'rows': rows,
            'totals': {'total': total}
        }

    def get_close_rate_trend(self, user_id=None):
        """Get monthly close rate for last 12 months."""
        today = date.today()
        start_date = date(today.year - 1, today.month, 1)

        query = org_query(Transaction).filter(
            Transaction.created_at >= start_date
        )
        query = self._apply_user_filter(query, Transaction, user_id)

        # Get monthly breakdown
        results = query.with_entities(
            extract('year', Transaction.created_at).label('year'),
            extract('month', Transaction.created_at).label('month'),
            Transaction.status,
            func.count(Transaction.id).label('count')
        ).group_by('year', 'month', Transaction.status).all()

        # Organize by month
        monthly_data = {}
        for year, month, status, count in results:
            key = f"{int(year)}-{int(month):02d}"
            if key not in monthly_data:
                monthly_data[key] = {'closed': 0, 'cancelled': 0, 'other': 0}
            if status == 'closed':
                monthly_data[key]['closed'] = count
            elif status == 'cancelled':
                monthly_data[key]['cancelled'] = count
            else:
                monthly_data[key]['other'] += count

        # Sort by month
        sorted_months = sorted(monthly_data.keys())

        chart_data = {
            'labels': sorted_months,
            'datasets': [
                {'name': 'Closed', 'values': [monthly_data[m]['closed'] for m in sorted_months]},
                {'name': 'Cancelled', 'values': [monthly_data[m]['cancelled'] for m in sorted_months]}
            ]
        }

        rows = []
        for month in sorted_months:
            data = monthly_data[month]
            total = data['closed'] + data['cancelled'] + data['other']
            close_rate = round((data['closed'] / total * 100), 1) if total > 0 else 0
            rows.append({
                'month': month,
                'closed_count': data['closed'],
                'cancelled_count': data['cancelled'],
                'total': total,
                'close_rate': f'{close_rate}%'
            })

        return {
            'chart_data': chart_data,
            'rows': rows
        }

    # =========================================================================
    # CONTACT REPORTS
    # =========================================================================

    def get_contact_engagement(self, date_range=None, user_id=None):
        """Get contact engagement health (hot/warm/cold)."""
        query = org_query(Contact)
        query = self._apply_user_filter(query, Contact, user_id)

        if date_range:
            query = self._apply_date_filter(query, Contact.created_at, date_range)

        contacts = query.all()
        today = date.today()

        # Categorize contacts
        hot = warm = cold = 0
        rows = []

        for contact in contacts:
            if contact.last_contact_date:
                days_since = (today - contact.last_contact_date).days
            else:
                days_since = 999  # Never contacted

            if days_since <= 30:
                status = 'hot'
                hot += 1
            elif days_since <= 90:
                status = 'warm'
                warm += 1
            else:
                status = 'cold'
                cold += 1

            groups = ', '.join([g.name for g in contact.groups]) if contact.groups else ''

            rows.append({
                'id': contact.id,
                'full_name': f'{contact.first_name} {contact.last_name}',
                'email': contact.email or '',
                'phone': contact.phone or '',
                'last_contact_date': contact.last_contact_date.strftime('%m/%d/%Y') if contact.last_contact_date else 'Never',
                'days_since_contact': days_since if days_since < 999 else None,
                'engagement_status': status,
                'potential_commission': float(contact.potential_commission) if contact.potential_commission else 0,
                'groups': groups
            })

        # Sort by days since contact descending
        rows.sort(key=lambda x: x['days_since_contact'] if x['days_since_contact'] else 9999, reverse=True)

        chart_data = {
            'labels': ['Hot (< 30 days)', 'Warm (30-90 days)', 'Cold (> 90 days)'],
            'values': [hot, warm, cold]
        }

        return {
            'chart_data': chart_data,
            'rows': rows,
            'totals': {'total': len(contacts), 'hot': hot, 'warm': warm, 'cold': cold}
        }

    def get_high_value_stale_contacts(self, min_commission=10000, days_threshold=30, user_id=None):
        """Get high-value contacts not contacted recently."""
        today = date.today()
        threshold_date = today - timedelta(days=days_threshold)

        query = org_query(Contact).filter(
            Contact.potential_commission >= min_commission,
            or_(
                Contact.last_contact_date < threshold_date,
                Contact.last_contact_date == None
            )
        )
        query = self._apply_user_filter(query, Contact, user_id)
        query = query.order_by(Contact.potential_commission.desc())

        contacts = query.all()

        rows = []
        total_commission = Decimal('0')
        for contact in contacts:
            if contact.last_contact_date:
                days_since = (today - contact.last_contact_date).days
            else:
                days_since = None

            groups = ', '.join([g.name for g in contact.groups]) if contact.groups else ''
            commission = contact.potential_commission or Decimal('0')
            total_commission += commission

            rows.append({
                'id': contact.id,
                'full_name': f'{contact.first_name} {contact.last_name}',
                'email': contact.email or '',
                'phone': contact.phone or '',
                'potential_commission': float(commission),
                'last_contact_date': contact.last_contact_date.strftime('%m/%d/%Y') if contact.last_contact_date else 'Never',
                'days_since_contact': days_since,
                'groups': groups
            })

        return {
            'rows': rows,
            'totals': {'total': len(contacts), 'total_commission': float(total_commission)}
        }

    def get_contact_group_distribution(self, user_id=None):
        """Get contact counts by group."""
        # Get all groups for this org with contact counts using a more efficient query
        from sqlalchemy import func

        # Build base query - if user_id is provided, filter contacts by user
        if user_id:
            # Join with Contact to filter by user_id
            results = db.session.query(
                ContactGroup.id,
                ContactGroup.name,
                ContactGroup.category,
                func.count(contact_groups.c.contact_id).label('contact_count')
            ).outerjoin(
                contact_groups, ContactGroup.id == contact_groups.c.group_id
            ).outerjoin(
                Contact, contact_groups.c.contact_id == Contact.id
            ).filter(
                ContactGroup.organization_id == current_user.organization_id,
                or_(Contact.user_id == user_id, Contact.id == None)
            ).group_by(
                ContactGroup.id, ContactGroup.name, ContactGroup.category
            ).all()
        else:
            # Query groups with contact counts
            results = db.session.query(
                ContactGroup.id,
                ContactGroup.name,
                ContactGroup.category,
                func.count(contact_groups.c.contact_id).label('contact_count')
            ).outerjoin(
                contact_groups, ContactGroup.id == contact_groups.c.group_id
            ).filter(
                ContactGroup.organization_id == current_user.organization_id
            ).group_by(
                ContactGroup.id, ContactGroup.name, ContactGroup.category
            ).all()

        rows = []
        chart_labels = []
        chart_values = []

        for group_id, name, category, count in results:
            if count > 0:
                rows.append({
                    'group_name': name,
                    'category': category or 'Uncategorized',
                    'count': count
                })
                chart_labels.append(name)
                chart_values.append(count)

        # Sort by count descending
        rows.sort(key=lambda x: x['count'], reverse=True)

        # Re-sort chart data to match rows order
        sorted_data = sorted(zip(chart_labels, chart_values), key=lambda x: x[1], reverse=True)
        chart_labels = [x[0] for x in sorted_data]
        chart_values = [x[1] for x in sorted_data]

        return {
            'chart_data': {
                'labels': chart_labels,
                'values': chart_values
            },
            'rows': rows,
            'totals': {'total': sum(chart_values)}
        }

    def get_new_contacts(self, date_range='this_month', user_id=None):
        """Get contacts created within the date range."""
        query = org_query(Contact)
        query = self._apply_user_filter(query, Contact, user_id)
        query = self._apply_date_filter(query, Contact.created_at, date_range)
        query = query.order_by(Contact.created_at.desc())

        contacts = query.all()

        rows = []
        for contact in contacts:
            groups = ', '.join([g.name for g in contact.groups]) if contact.groups else ''
            created_by = ''
            if contact.created_by:
                created_by = f'{contact.created_by.first_name} {contact.created_by.last_name}'

            rows.append({
                'id': contact.id,
                'full_name': f'{contact.first_name} {contact.last_name}',
                'email': contact.email or '',
                'phone': contact.phone or '',
                'created_at': contact.created_at.strftime('%m/%d/%Y %H:%M') if contact.created_at else '',
                'groups': groups,
                'created_by': created_by
            })

        # Group by date for chart
        date_counts = {}
        for contact in contacts:
            if contact.created_at:
                date_key = contact.created_at.strftime('%Y-%m-%d')
                date_counts[date_key] = date_counts.get(date_key, 0) + 1

        sorted_dates = sorted(date_counts.keys())

        return {
            'chart_data': {
                'labels': sorted_dates,
                'values': [date_counts[d] for d in sorted_dates]
            },
            'rows': rows,
            'totals': {'total': len(contacts)}
        }

    # =========================================================================
    # ACTIVITY REPORTS
    # =========================================================================

    def get_task_completion(self, date_range='last_8_weeks', user_id=None):
        """Get task completion by week."""
        today = date.today()
        today_datetime = datetime.combine(today, datetime.min.time())

        if date_range == 'last_8_weeks':
            start_date = today - timedelta(weeks=8)
        elif date_range == 'last_4_weeks':
            start_date = today - timedelta(weeks=4)
        else:
            start_date = today - timedelta(weeks=8)

        query = org_query(Task).filter(Task.created_at >= start_date)
        query = self._apply_user_filter(query, Task, user_id)
        tasks = query.all()

        # Group by week
        weekly_data = {}
        for task in tasks:
            if task.created_at:
                week_start = task.created_at.date() - timedelta(days=task.created_at.weekday())
                week_key = week_start.strftime('%Y-%m-%d')

                if week_key not in weekly_data:
                    weekly_data[week_key] = {'completed': 0, 'pending': 0, 'overdue': 0}

                if task.status == 'completed':
                    weekly_data[week_key]['completed'] += 1
                elif task.status == 'pending':
                    # Compare datetime with datetime
                    if task.due_date and task.due_date < today_datetime:
                        weekly_data[week_key]['overdue'] += 1
                    else:
                        weekly_data[week_key]['pending'] += 1

        sorted_weeks = sorted(weekly_data.keys())

        chart_data = {
            'labels': sorted_weeks,
            'datasets': [
                {'name': 'Completed', 'values': [weekly_data[w]['completed'] for w in sorted_weeks]},
                {'name': 'Pending', 'values': [weekly_data[w]['pending'] for w in sorted_weeks]},
                {'name': 'Overdue', 'values': [weekly_data[w]['overdue'] for w in sorted_weeks]}
            ]
        }

        rows = []
        for week in sorted_weeks:
            data = weekly_data[week]
            total = data['completed'] + data['pending'] + data['overdue']
            completion_rate = round((data['completed'] / total * 100), 1) if total > 0 else 0
            rows.append({
                'week': week,
                'completed': data['completed'],
                'pending': data['pending'],
                'overdue': data['overdue'],
                'total': total,
                'completion_rate': f'{completion_rate}%'
            })

        total_completed = sum(d['completed'] for d in weekly_data.values())
        total_pending = sum(d['pending'] for d in weekly_data.values())
        total_overdue = sum(d['overdue'] for d in weekly_data.values())

        return {
            'chart_data': chart_data,
            'rows': rows,
            'totals': {
                'completed': total_completed,
                'pending': total_pending,
                'overdue': total_overdue,
                'total': total_completed + total_pending + total_overdue
            }
        }

    def get_overdue_tasks(self, user_id=None):
        """Get all overdue tasks."""
        today = date.today()
        today_datetime = datetime.combine(today, datetime.min.time())
        now = datetime.utcnow()

        query = org_query(Task).join(
            Contact, Task.contact_id == Contact.id
        ).join(
            TaskType, Task.type_id == TaskType.id
        ).filter(
            Task.status == 'pending',
            Task.due_date < today_datetime
        )
        query = self._apply_user_filter(query, Task, user_id)
        query = query.order_by(Task.priority.desc(), Task.due_date)

        tasks = query.all()

        rows = []
        for task in tasks:
            # Calculate days overdue using datetime comparison
            days_overdue = (now - task.due_date).days if task.due_date else 0
            rows.append({
                'id': task.id,
                'subject': task.subject or '',
                'contact_name': f'{task.contact.first_name} {task.contact.last_name}' if task.contact else '',
                'contact_id': task.contact_id,
                'type': task.task_type.name if task.task_type else '',
                'due_date': task.due_date.strftime('%m/%d/%Y') if task.due_date else '',
                'days_overdue': days_overdue,
                'priority': task.priority or 'medium'
            })

        return {
            'rows': rows,
            'totals': {'total': len(tasks)}
        }

    def get_interaction_log(self, date_range='last_30_days', user_id=None):
        """Get recent interactions."""
        query = org_query(Interaction).join(
            Contact, Interaction.contact_id == Contact.id
        )
        query = self._apply_user_filter(query, Interaction, user_id)
        query = self._apply_date_filter(query, Interaction.date, date_range)
        query = query.order_by(Interaction.date.desc())

        interactions = query.limit(100).all()

        rows = []
        for interaction in interactions:
            rows.append({
                'id': interaction.id,
                'contact_name': f'{interaction.contact.first_name} {interaction.contact.last_name}' if interaction.contact else '',
                'contact_id': interaction.contact_id,
                'type': interaction.type or '',
                'date': interaction.date.strftime('%m/%d/%Y %H:%M') if interaction.date else '',
                'notes': interaction.notes[:100] + '...' if interaction.notes and len(interaction.notes) > 100 else (interaction.notes or ''),
                'follow_up_date': interaction.follow_up_date.strftime('%m/%d/%Y') if interaction.follow_up_date else ''
            })

        return {
            'rows': rows,
            'totals': {'total': len(interactions)}
        }

    # =========================================================================
    # DOCUMENT REPORTS
    # =========================================================================

    def get_document_status(self, user_id=None):
        """Get document signing status overview."""
        query = org_query(TransactionDocument).join(
            Transaction, TransactionDocument.transaction_id == Transaction.id
        )
        
        # Filter by transaction creator if user_id provided
        if user_id:
            query = query.filter(Transaction.created_by_id == user_id)

        documents = query.all()

        # Count by status
        status_counts = {}
        for doc in documents:
            status = doc.status or 'unknown'
            status_counts[status] = status_counts.get(status, 0) + 1

        status_order = ['pending', 'draft', 'sent', 'partially_signed', 'signed', 'voided']
        chart_labels = []
        chart_values = []
        for status in status_order:
            if status in status_counts:
                chart_labels.append(status.replace('_', ' ').title())
                chart_values.append(status_counts[status])

        rows = []
        for doc in documents:
            # Count signatures
            signatures = DocumentSignature.query.filter_by(document_id=doc.id).all()
            signed = len([s for s in signatures if s.status == 'signed'])
            total_sigs = len(signatures)
            progress = f'{signed}/{total_sigs}' if total_sigs > 0 else 'N/A'

            rows.append({
                'id': doc.id,
                'document_name': doc.template_name or 'Untitled',
                'transaction_address': doc.transaction.street_address if doc.transaction else '',
                'transaction_id': doc.transaction_id,
                'status': doc.status or 'unknown',
                'sent_at': doc.sent_at.strftime('%m/%d/%Y') if doc.sent_at else '',
                'signers_progress': progress
            })

        return {
            'chart_data': {
                'labels': chart_labels,
                'values': chart_values
            },
            'rows': rows,
            'totals': {'total': len(documents)}
        }

    def get_pending_signatures(self, user_id=None):
        """Get documents awaiting signatures."""
        today = date.today()

        query = DocumentSignature.query.join(
            TransactionDocument, DocumentSignature.document_id == TransactionDocument.id
        ).join(
            Transaction, TransactionDocument.transaction_id == Transaction.id
        ).filter(
            Transaction.organization_id == current_user.organization_id,
            DocumentSignature.status.in_(['pending', 'sent', 'viewed'])
        )
        
        # Filter by transaction creator if user_id provided
        if user_id:
            query = query.filter(Transaction.created_by_id == user_id)
        
        query = query.order_by(DocumentSignature.sent_at)

        signatures = query.all()

        rows = []
        for sig in signatures:
            days_waiting = (datetime.utcnow() - sig.sent_at).days if sig.sent_at else 0
            rows.append({
                'id': sig.id,
                'document_name': sig.document.template_name if sig.document else '',
                'document_id': sig.document_id,
                'transaction_address': sig.document.transaction.street_address if sig.document and sig.document.transaction else '',
                'transaction_id': sig.document.transaction_id if sig.document else None,
                'signer_name': sig.signer_name or '',
                'signer_email': sig.signer_email or '',
                'status': sig.status,
                'sent_at': sig.sent_at.strftime('%m/%d/%Y') if sig.sent_at else '',
                'days_waiting': days_waiting
            })

        return {
            'rows': rows,
            'totals': {'total': len(signatures)}
        }

    # =========================================================================
    # NEW STREAMLINED REPORTS (2026 Redesign)
    # =========================================================================

    def get_hot_leads_scorecard(self, user_id=None):
        """
        Get prioritized contact list ranked by potential_commission × days_since_contact.
        Combines contact engagement + high-value stale contacts into one actionable view.
        """
        today = date.today()
        query = org_query(Contact)
        query = self._apply_user_filter(query, Contact, user_id)
        contacts = query.all()

        hot = warm = cold = 0
        rows = []

        for contact in contacts:
            if contact.last_contact_date:
                days_since = (today - contact.last_contact_date).days
            else:
                days_since = 999  # Never contacted - high priority

            # Engagement status
            if days_since <= 30:
                status = 'hot'
                hot += 1
            elif days_since <= 90:
                status = 'warm'
                warm += 1
            else:
                status = 'cold'
                cold += 1

            # Priority score: commission × days (higher = more urgent to contact)
            commission = float(contact.potential_commission) if contact.potential_commission else 0
            priority_score = commission * min(days_since, 365)  # Cap at 1 year

            groups = ', '.join([g.name for g in contact.groups]) if contact.groups else ''

            rows.append({
                'id': contact.id,
                'full_name': f'{contact.first_name} {contact.last_name}',
                'email': contact.email or '',
                'phone': contact.phone or '',
                'potential_commission': commission,
                'last_contact_date': contact.last_contact_date.strftime('%m/%d/%Y') if contact.last_contact_date else 'Never',
                'days_since_contact': days_since if days_since < 999 else None,
                'engagement_status': status,
                'priority_score': priority_score,
                'groups': groups
            })

        # Sort by priority score descending (highest priority first)
        rows.sort(key=lambda x: x['priority_score'], reverse=True)

        chart_data = {
            'labels': ['Hot (< 30 days)', 'Warm (30-90 days)', 'Cold (> 90 days)'],
            'values': [hot, warm, cold]
        }

        return {
            'chart_data': chart_data,
            'rows': rows,
            'totals': {
                'total': len(contacts),
                'hot': hot,
                'warm': warm,
                'cold': cold,
                'priority_contacts': len([r for r in rows if r['priority_score'] > 50000])
            }
        }

    def get_at_risk_deals(self, user_id=None):
        """
        Get deals at risk: stale (no activity 14+ days), past expected close, or missing docs.
        Combines multiple warning signals into one actionable view.
        """
        today = date.today()
        now = datetime.utcnow()
        stale_threshold = now - timedelta(days=14)

        query = org_query(Transaction).join(
            TransactionType, Transaction.transaction_type_id == TransactionType.id
        ).filter(
            Transaction.status.in_(['preparing_to_list', 'active', 'under_contract'])
        )
        query = self._apply_user_filter(query, Transaction, user_id)
        transactions = query.all()

        # Fetch participants
        tx_ids = [t.id for t in transactions]
        participants = TransactionParticipant.query.filter(
            TransactionParticipant.transaction_id.in_(tx_ids),
            TransactionParticipant.is_primary == True
        ).all() if tx_ids else []
        participant_map = {p.transaction_id: p for p in participants}

        rows = []
        for tx in transactions:
            risk_reasons = []
            days_since_update = (now - tx.updated_at).days if tx.updated_at else 0

            # Check: Stale (no activity 14+ days)
            if tx.updated_at and tx.updated_at < stale_threshold:
                risk_reasons.append(f'No activity {days_since_update} days')

            # Check: Past expected close date
            if tx.expected_close_date and tx.expected_close_date < today:
                days_past = (today - tx.expected_close_date).days
                risk_reasons.append(f'Past close date by {days_past} days')

            # Only include if there's at least one risk factor
            if not risk_reasons:
                continue

            participant = participant_map.get(tx.id)
            rows.append({
                'id': tx.id,
                'street_address': tx.street_address,
                'city': tx.city or '',
                'client_name': participant.display_name if participant else '',
                'status': tx.status,
                'risk_reason': '; '.join(risk_reasons),
                'days_since_update': days_since_update,
                'expected_close_date': tx.expected_close_date.strftime('%m/%d/%Y') if tx.expected_close_date else ''
            })

        # Sort by most urgent (longest time without activity)
        rows.sort(key=lambda x: x['days_since_update'], reverse=True)

        return {
            'rows': rows,
            'totals': {'total': len(rows)}
        }

    def get_weekly_activity_summary(self, user_id=None):
        """
        Get weekly activity digest: interactions this week, contacts touched, untouched high-value.
        """
        today = date.today()
        week_start = today - timedelta(days=7)

        # Get interactions from last 7 days
        interaction_query = org_query(Interaction).filter(
            Interaction.date >= week_start
        )
        interaction_query = self._apply_user_filter(interaction_query, Interaction, user_id)
        interactions = interaction_query.all()

        # Count by type
        type_counts = {}
        touched_contact_ids = set()
        for interaction in interactions:
            itype = interaction.type or 'other'
            type_counts[itype] = type_counts.get(itype, 0) + 1
            touched_contact_ids.add(interaction.contact_id)

        # Get high-value contacts not touched this week
        contact_query = org_query(Contact).filter(
            Contact.potential_commission >= 5000,
            ~Contact.id.in_(touched_contact_ids) if touched_contact_ids else True
        )
        contact_query = self._apply_user_filter(contact_query, Contact, user_id)
        untouched_hv = contact_query.count()

        chart_data = {
            'labels': list(type_counts.keys()) if type_counts else ['No Activity'],
            'values': list(type_counts.values()) if type_counts else [0]
        }

        rows = [{
            'metric': 'Total Interactions',
            'value': len(interactions)
        }, {
            'metric': 'Contacts Touched',
            'value': len(touched_contact_ids)
        }, {
            'metric': 'High-Value Untouched',
            'value': untouched_hv
        }]

        # Add breakdown by type
        for itype, count in sorted(type_counts.items(), key=lambda x: x[1], reverse=True):
            rows.append({
                'metric': f'{itype.title()} Interactions',
                'value': count
            })

        return {
            'chart_data': chart_data,
            'rows': rows,
            'totals': {
                'total_interactions': len(interactions),
                'contacts_touched': len(touched_contact_ids),
                'untouched_high_value': untouched_hv,
                'weekly_touches': len(interactions)
            }
        }

    def get_report_preview_metrics(self, user_id=None):
        """
        Get preview metrics for all reports on the landing page.
        Returns a dict of metric_key: value for live badges.
        """
        today = date.today()
        now = datetime.utcnow()
        org_id = current_user.organization_id

        # Base queries with optional user filtering
        tx_query = Transaction.query.filter(Transaction.organization_id == org_id)
        contact_query = Contact.query.filter(Contact.organization_id == org_id)
        task_query = Task.query.filter(Task.organization_id == org_id)

        if user_id:
            tx_query = tx_query.filter(Transaction.created_by_id == user_id)
            contact_query = contact_query.filter(Contact.user_id == user_id)
            task_query = task_query.filter(Task.assigned_to_id == user_id)

        # Active deals
        active_statuses = ['preparing_to_list', 'active', 'under_contract']
        active_deals = tx_query.filter(Transaction.status.in_(active_statuses)).count()

        # Closing soon (this month)
        month_end = date(today.year, today.month + 1, 1) - timedelta(days=1) if today.month < 12 else date(today.year + 1, 1, 1) - timedelta(days=1)
        closing_soon = tx_query.filter(
            Transaction.expected_close_date >= today,
            Transaction.expected_close_date <= month_end,
            Transaction.status.notin_(['closed', 'cancelled'])
        ).count()

        # At-risk deals (stale 14+ days)
        stale_threshold = now - timedelta(days=14)
        at_risk = tx_query.filter(
            Transaction.status.in_(active_statuses),
            Transaction.updated_at < stale_threshold
        ).count()

        # Priority contacts (cold with $5k+ commission)
        threshold_date = today - timedelta(days=90)
        priority_contacts = contact_query.filter(
            Contact.potential_commission >= 5000,
            or_(Contact.last_contact_date < threshold_date, Contact.last_contact_date == None)
        ).count()

        # Overdue tasks
        today_datetime = datetime.combine(today, datetime.min.time())
        overdue_tasks = task_query.filter(
            Task.status == 'pending',
            Task.due_date < today_datetime
        ).count()

        # Weekly touches (last 7 days interactions)
        week_start = today - timedelta(days=7)
        interaction_query = Interaction.query.filter(
            Interaction.organization_id == org_id,
            Interaction.date >= week_start
        )
        if user_id:
            interaction_query = interaction_query.filter(Interaction.user_id == user_id)
        weekly_touches = interaction_query.count()

        # Pending docs (count DISTINCT documents with at least one pending signature)
        pending_docs = db.session.query(func.count(distinct(TransactionDocument.id))).join(
            DocumentSignature, TransactionDocument.id == DocumentSignature.document_id
        ).join(
            Transaction, TransactionDocument.transaction_id == Transaction.id
        ).filter(
            Transaction.organization_id == org_id,
            DocumentSignature.status.in_(['pending', 'sent', 'viewed'])
        ).scalar()

        # Closed YTD
        year_start = date(today.year, 1, 1)
        closed_ytd = tx_query.filter(
            Transaction.status == 'closed',
            Transaction.actual_close_date >= year_start
        ).count()

        return {
            'active_deals': active_deals,
            'closing_soon': closing_soon,
            'at_risk': at_risk,
            'priority_contacts': priority_contacts,
            'overdue_tasks': overdue_tasks,
            'weekly_touches': weekly_touches,
            'pending_docs': pending_docs,
            'closed_ytd': closed_ytd
        }

    # =========================================================================
    # SCORECARD / KPI REPORT
    # =========================================================================

    def get_agent_scorecard(self, user_id=None):
        """Get key performance indicators for the agent/org."""
        today = date.today()
        year_start = date(today.year, 1, 1)
        month_start = date(today.year, today.month, 1)
        # Convert to datetime for comparison with DateTime columns
        month_start_datetime = datetime.combine(month_start, datetime.min.time())
        today_datetime = datetime.combine(today, datetime.min.time())

        org_id = current_user.organization_id

        # Build base queries with optional user filtering
        tx_query = Transaction.query.filter(Transaction.organization_id == org_id)
        task_query = Task.query.filter(Task.organization_id == org_id)
        contact_query = Contact.query.filter(Contact.organization_id == org_id)
        
        if user_id:
            tx_query = tx_query.filter(Transaction.created_by_id == user_id)
            task_query = task_query.filter(Task.assigned_to_id == user_id)
            contact_query = contact_query.filter(Contact.user_id == user_id)

        # Closed YTD
        closed_ytd = tx_query.filter(
            Transaction.status == 'closed',
            Transaction.actual_close_date >= year_start
        ).count()

        # Pipeline value (sum of potential commission from active contacts linked to active transactions)
        active_statuses = ['preparing_to_list', 'active', 'under_contract']
        pipeline_value = tx_query.filter(
            Transaction.status.in_(active_statuses)
        ).count()  # Simplified - just count active deals

        # Active deals
        active_deals = tx_query.filter(
            Transaction.status.in_(active_statuses)
        ).count()

        # Tasks completed this month (completed_at is DateTime)
        tasks_completed = task_query.filter(
            Task.status == 'completed',
            Task.completed_at >= month_start_datetime
        ).count()

        # Total contacts
        total_contacts = contact_query.count()

        # Overdue tasks (due_date is DateTime)
        overdue_tasks = task_query.filter(
            Task.status == 'pending',
            Task.due_date < today_datetime
        ).count()

        return {
            'kpis': [
                {'key': 'closed_ytd', 'label': 'Closed YTD', 'value': closed_ytd, 'icon': 'fa-handshake', 'color': 'emerald'},
                {'key': 'active_deals', 'label': 'Active Deals', 'value': active_deals, 'icon': 'fa-home', 'color': 'blue'},
                {'key': 'tasks_completed_month', 'label': 'Tasks This Month', 'value': tasks_completed, 'icon': 'fa-check-circle', 'color': 'purple'},
                {'key': 'total_contacts', 'label': 'Total Contacts', 'value': total_contacts, 'icon': 'fa-users', 'color': 'amber'},
                {'key': 'overdue_tasks', 'label': 'Overdue Tasks', 'value': overdue_tasks, 'icon': 'fa-exclamation-circle', 'color': 'red' if overdue_tasks > 0 else 'slate'}
            ]
        }

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

    def _apply_date_filter(self, query, field, date_range):
        """Apply date range filter to query."""
        today = date.today()

        if date_range == 'today':
            return query.filter(func.date(field) == today)
        elif date_range == 'this_week':
            week_start = today - timedelta(days=today.weekday())
            return query.filter(field >= week_start)
        elif date_range == 'this_month':
            month_start = date(today.year, today.month, 1)
            return query.filter(field >= month_start)
        elif date_range == 'this_quarter':
            quarter = (today.month - 1) // 3
            quarter_start = date(today.year, quarter * 3 + 1, 1)
            return query.filter(field >= quarter_start)
        elif date_range == 'this_year':
            year_start = date(today.year, 1, 1)
            return query.filter(field >= year_start)
        elif date_range == 'last_7_days':
            return query.filter(field >= today - timedelta(days=7))
        elif date_range == 'last_30_days':
            return query.filter(field >= today - timedelta(days=30))
        elif date_range == 'last_90_days':
            return query.filter(field >= today - timedelta(days=90))
        elif date_range == 'last_12_months':
            return query.filter(field >= date(today.year - 1, today.month, 1))
        elif date_range == 'last_4_weeks':
            return query.filter(field >= today - timedelta(weeks=4))
        elif date_range == 'last_8_weeks':
            return query.filter(field >= today - timedelta(weeks=8))

        return query


# Singleton instance
report_service = ReportService()

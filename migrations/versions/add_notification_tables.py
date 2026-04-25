"""Add notifications and user_notification_preferences tables

Revision ID: add_notification_tables
Revises: add_market_insights_tables
Create Date: 2026-04-25 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'add_notification_tables'
down_revision = 'add_market_insights_tables'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'notifications' not in tables:
        op.create_table(
            'notifications',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=False),
            sa.Column('title', sa.String(length=200), nullable=False),
            sa.Column('body', sa.Text(), nullable=True),
            sa.Column('icon', sa.String(length=60), server_default='fa-bell'),
            sa.Column('action_url', sa.String(length=500), nullable=True),
            sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('0')),
            sa.Column('read_at', sa.DateTime(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id', name='pk_notifications'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_notifications_org',
                                    ondelete='RESTRICT'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                    name='fk_notifications_user',
                                    ondelete='CASCADE'),
        )
        op.create_index('ix_notifications_org_id', 'notifications',
                        ['organization_id'], unique=False)
        op.create_index('ix_notifications_user_id', 'notifications',
                        ['user_id'], unique=False)
        op.create_index('ix_notifications_category', 'notifications',
                        ['category'], unique=False)
        op.create_index('ix_notifications_is_read', 'notifications',
                        ['is_read'], unique=False)
        op.create_index('ix_notifications_created_at', 'notifications',
                        ['created_at'], unique=False)

    if 'user_notification_preferences' not in tables:
        op.create_table(
            'user_notification_preferences',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('user_id', sa.Integer(), nullable=False),
            sa.Column('organization_id', sa.Integer(), nullable=False),
            sa.Column('category', sa.String(length=50), nullable=False),
            sa.Column('in_app_enabled', sa.Boolean(), nullable=False,
                      server_default=sa.text('1')),
            sa.Column('email_enabled', sa.Boolean(), nullable=False,
                      server_default=sa.text('1')),
            sa.Column('updated_at', sa.DateTime(), nullable=False,
                      server_default=sa.text('CURRENT_TIMESTAMP')),
            sa.PrimaryKeyConstraint('id', name='pk_user_notification_preferences'),
            sa.ForeignKeyConstraint(['user_id'], ['user.id'],
                                    name='fk_user_notif_pref_user',
                                    ondelete='CASCADE'),
            sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'],
                                    name='fk_user_notif_pref_org',
                                    ondelete='RESTRICT'),
            sa.UniqueConstraint('user_id', 'category',
                                name='uq_user_notification_pref'),
        )
        op.create_index('ix_user_notif_pref_user_id',
                        'user_notification_preferences',
                        ['user_id'], unique=False)
        op.create_index('ix_user_notif_pref_org_id',
                        'user_notification_preferences',
                        ['organization_id'], unique=False)


def downgrade():
    conn = op.get_bind()
    inspector = inspect(conn)
    tables = inspector.get_table_names()

    if 'user_notification_preferences' in tables:
        op.drop_index('ix_user_notif_pref_org_id',
                      table_name='user_notification_preferences')
        op.drop_index('ix_user_notif_pref_user_id',
                      table_name='user_notification_preferences')
        op.drop_table('user_notification_preferences')

    if 'notifications' in tables:
        op.drop_index('ix_notifications_created_at', table_name='notifications')
        op.drop_index('ix_notifications_is_read', table_name='notifications')
        op.drop_index('ix_notifications_category', table_name='notifications')
        op.drop_index('ix_notifications_user_id', table_name='notifications')
        op.drop_index('ix_notifications_org_id', table_name='notifications')
        op.drop_table('notifications')

export type NotificationCategory =
  | 'WARRANTY_EXPIRING'
  | 'PROCESSING_COMPLETE'
  | 'REVIEW_REQUIRED'
  | 'CLAIM_READY'
  | 'SYSTEM';

export type NotificationStatus =
  | 'SCHEDULED'
  | 'SENT'
  | 'READ'
  | 'CANCELLED'
  | 'FAILED'
  | 'SUPPRESSED';

export interface NotificationOut {
  id: number;
  category: NotificationCategory;
  title: string;
  body: string | null;
  purchase_id: number | null;
  claim_id: number | null;
  expiry_date_snapshot: string | null;
  reminder_type: string | null;
  status: NotificationStatus;
  scheduled_at: string | null;
  sent_at: string | null;
  read_at: string | null;
  attempts: number;
  last_error: string | null;
  next_retry_at: string | null;
  created_at: string | null;
}

export interface NotificationsPage {
  items: NotificationOut[];
  unread_count: number;
}

export interface ChannelStatus {
  provider: string;
  configured: boolean;
  note?: string;
}

export interface NotificationPreferencesOut {
  email_enabled: boolean;
  push_enabled: boolean;
  timezone: string;
  channels: { email: ChannelStatus; push: ChannelStatus };
}

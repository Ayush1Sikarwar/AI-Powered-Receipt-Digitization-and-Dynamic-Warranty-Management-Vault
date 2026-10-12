import { api } from './api';
import type {
  NotificationOut,
  NotificationPreferencesOut,
  NotificationsPage,
} from '../types/notifications';

export async function listNotifications(
  params: { unread?: boolean; page_size?: number } = {},
): Promise<NotificationsPage> {
  const qs = new URLSearchParams();
  if (params.unread) qs.set('unread', 'true');
  if (params.page_size) qs.set('page_size', String(params.page_size));
  const s = qs.toString();
  return api<NotificationsPage>(`/notifications${s ? `?${s}` : ''}`);
}

export async function markRead(id: number): Promise<NotificationOut> {
  return api<NotificationOut>(`/notifications/${id}/read`, { method: 'PATCH' });
}

export async function markAllRead(): Promise<{ marked_read: number }> {
  return api<{ marked_read: number }>('/notifications/read-all', { method: 'POST' });
}

export async function getNotificationPreferences(): Promise<NotificationPreferencesOut> {
  return api<NotificationPreferencesOut>('/notifications/preferences');
}

export async function updateNotificationPreferences(payload: {
  email_enabled?: boolean;
  push_enabled?: boolean;
}): Promise<NotificationPreferencesOut> {
  return api<NotificationPreferencesOut>('/notifications/preferences', {
    method: 'PUT',
    body: payload,
  });
}

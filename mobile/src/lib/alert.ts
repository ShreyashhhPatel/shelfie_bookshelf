/**
 * Cross-platform alerts.
 *
 * react-native-web ships `Alert.alert` as an empty function -- not a warning,
 * not a throw, a no-op that returns undefined. Every error the app reported
 * through it was therefore *completely silent* on web: permission denials,
 * failed uploads, failed deletes. The code looked correct and told the user
 * nothing.
 *
 * These wrappers are the only alert path the app uses. Nothing should import
 * `Alert` from react-native directly.
 */

import { Alert, Platform } from 'react-native';

/** True when RN's Alert is the react-native-web stub rather than a real one. */
const ALERT_IS_NOOP = Platform.OS === 'web';

export function notify(title: string, message?: string): void {
  if (!ALERT_IS_NOOP) {
    Alert.alert(title, message);
    return;
  }

  const text = message ? `${title}\n\n${message}` : title;
  if (typeof globalThis.alert === 'function') {
    globalThis.alert(text);
  } else {
    // Last resort: a browser with alerts suppressed still gets a console
    // record, which beats the silence this module exists to fix.
    console.warn(text);
  }
}

/**
 * Ask a yes/no question. Resolves true if the user agreed.
 *
 * Destructive by default, since every current caller is a delete or discard.
 */
export function confirm(
  title: string,
  message: string,
  confirmLabel = 'OK',
): Promise<boolean> {
  if (ALERT_IS_NOOP) {
    const text = `${title}\n\n${message}`;
    return Promise.resolve(
      typeof globalThis.confirm === 'function' ? globalThis.confirm(text) : true,
    );
  }

  return new Promise((resolve) => {
    Alert.alert(title, message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: confirmLabel, style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

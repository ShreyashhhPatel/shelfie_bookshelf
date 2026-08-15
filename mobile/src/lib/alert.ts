import { Alert, Platform } from "react-native";

type AlertButton = {
  text?: string;
  style?: "default" | "cancel" | "destructive";
  onPress?: () => void;
};

/**
 * react-native-web ships `Alert.alert` as an empty no-op, so on web every
 * error message and confirmation in this app would silently do nothing --
 * a failed upload looked identical to no tap at all. Map to the browser's
 * native dialogs there and defer to the real Alert everywhere else.
 */
export function alert(title: string, message?: string, buttons?: AlertButton[]): void {
  if (Platform.OS !== "web") {
    Alert.alert(title, message, buttons);
    return;
  }

  const body = [title, message].filter(Boolean).join("\n\n");
  const actions = (buttons ?? []).filter((b) => b.style !== "cancel");

  // Nothing to confirm -- just report it.
  if (actions.length === 0) {
    window.alert(body);
    return;
  }

  // RN can stack several actions; the browser gives us one OK. Offer the
  // last action (the affirmative one in every call site here), and only
  // make it cancellable if the caller actually provided a cancel button.
  const action = actions[actions.length - 1];
  const cancellable = (buttons ?? []).some((b) => b.style === "cancel");

  if (cancellable ? window.confirm(body) : (window.alert(body), true)) {
    action.onPress?.();
  }
}

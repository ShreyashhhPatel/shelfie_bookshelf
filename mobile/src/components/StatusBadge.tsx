import { StyleSheet, Text, View } from "react-native";

import type { DetectionStatus } from "../api/types";

const STATUS_META: Record<DetectionStatus, { label: string; color: string; bg: string }> = {
  auto_added: { label: "Added", color: "#1f7a4d", bg: "#e5f6ec" },
  confirmed: { label: "Added", color: "#1f7a4d", bg: "#e5f6ec" },
  needs_review: { label: "Needs review", color: "#a15c00", bg: "#fff2df" },
  unmatched: { label: "Not in catalog", color: "#6b4fa0", bg: "#f1ecfa" },
  unreadable: { label: "Couldn't read", color: "#8a3b3b", bg: "#fbeaea" },
  failed: { label: "Failed", color: "#8a3b3b", bg: "#fbeaea" },
  discarded: { label: "Discarded", color: "#6b7684", bg: "#eef1f4" },
};

export default function StatusBadge({ status }: { status: DetectionStatus }) {
  const meta = STATUS_META[status];
  return (
    <View style={[styles.badge, { backgroundColor: meta.bg }]}>
      <Text style={[styles.text, { color: meta.color }]}>{meta.label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999, alignSelf: "flex-start" },
  text: { fontSize: 12, fontWeight: "600" },
});

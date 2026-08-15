import { useFocusEffect } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useState } from "react";
import {
  ActivityIndicator,
  Image,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError, getScan } from "../api/client";
import type { Detection, Scan } from "../api/types";
import StatusBadge from "../components/StatusBadge";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Results">;

const ADDED_STATUSES = new Set(["auto_added", "confirmed"]);
const REVIEW_STATUSES = new Set(["needs_review", "unmatched", "unreadable", "failed"]);

function DetectionRow({ detection, onPress }: { detection: Detection; onPress: () => void }) {
  return (
    <TouchableOpacity style={styles.row} onPress={onPress}>
      {detection.crop_url ? (
        <Image source={{ uri: detection.crop_url }} style={styles.thumb} />
      ) : (
        <View style={[styles.thumb, styles.thumbPlaceholder]} />
      )}
      <View style={styles.rowText}>
        <Text style={styles.rowTitle} numberOfLines={1}>
          {detection.read_title || "Unreadable spine"}
        </Text>
        {!!detection.read_author && (
          <Text style={styles.rowAuthor} numberOfLines={1}>
            {detection.read_author}
          </Text>
        )}
      </View>
      <StatusBadge status={detection.status} />
    </TouchableOpacity>
  );
}

export default function ResultsScreen({ route, navigation }: Props) {
  const { scanId } = route.params;
  const [scan, setScan] = useState<Scan | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setError(null);
    try {
      const data = await getScan(scanId);
      setScan(data);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load this scan.");
    } finally {
      setLoading(false);
    }
  }, [scanId]);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#2f6690" />
      </View>
    );
  }

  if (error || !scan) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>{error || "Couldn't load this scan."}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={load}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (scan.status === "failed") {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>Something went wrong processing that photo.</Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => navigation.navigate("Scan")}>
          <Text style={styles.retryButtonText}>Scan Again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  if (scan.empty_result) {
    return (
      <View style={styles.centered}>
        <Text style={styles.emptyTitle}>No books found</Text>
        <Text style={styles.emptyTips}>
          Try getting closer so spines fill the frame, make sure the shelf is well-lit, and hold the phone steady.
        </Text>
        <TouchableOpacity style={styles.retryButton} onPress={() => navigation.navigate("Scan")}>
          <Text style={styles.retryButtonText}>Try Again</Text>
        </TouchableOpacity>
      </View>
    );
  }

  const added = scan.detections.filter((d) => ADDED_STATUSES.has(d.status));
  const needsReview = scan.detections.filter((d) => REVIEW_STATUSES.has(d.status));

  return (
    <ScrollView
      style={styles.container}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={load} />}
    >
      {scan.image_url && <Image source={{ uri: scan.image_url }} style={styles.heroImage} />}

      {typeof scan.timings.total_ms === "number" && (
        <Text style={styles.timing}>Processed in {(scan.timings.total_ms / 1000).toFixed(1)}s</Text>
      )}

      {needsReview.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Needs your review ({needsReview.length})</Text>
          {needsReview.map((d) => (
            <DetectionRow
              key={d.id}
              detection={d}
              onPress={() => navigation.navigate("Review", { scanId: scan.id, detectionId: d.id })}
            />
          ))}
        </View>
      )}

      {added.length > 0 && (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Added to your library ({added.length})</Text>
          {added.map((d) => (
            <DetectionRow key={d.id} detection={d} onPress={() => {}} />
          ))}
        </View>
      )}

      <TouchableOpacity style={styles.scanAgainButton} onPress={() => navigation.navigate("Scan")}>
        <Text style={styles.scanAgainText}>Scan Another Shelf</Text>
      </TouchableOpacity>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  heroImage: { width: "100%", height: 180 },
  timing: { textAlign: "center", color: "#9aa5b1", fontSize: 12, marginTop: 8 },
  section: { paddingHorizontal: 16, marginTop: 20 },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: "#1a2b3c", marginBottom: 10 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: 12,
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#e4e9ee",
  },
  thumb: { width: 40, height: 56, borderRadius: 4, backgroundColor: "#eef3f7" },
  thumbPlaceholder: { alignItems: "center", justifyContent: "center" },
  rowText: { flex: 1 },
  rowTitle: { fontSize: 15, fontWeight: "600", color: "#1a2b3c" },
  rowAuthor: { fontSize: 13, color: "#5b6b7a", marginTop: 2 },
  errorText: { fontSize: 16, color: "#8a3b3b", textAlign: "center" },
  emptyTitle: { fontSize: 20, fontWeight: "700", color: "#1a2b3c" },
  emptyTips: { fontSize: 14, color: "#5b6b7a", textAlign: "center" },
  retryButton: { backgroundColor: "#2f6690", paddingHorizontal: 24, paddingVertical: 12, borderRadius: 10 },
  retryButtonText: { color: "#fff", fontWeight: "600" },
  scanAgainButton: { margin: 20, padding: 14, borderRadius: 10, backgroundColor: "#eef3f7", alignItems: "center" },
  scanAgainText: { color: "#2f6690", fontWeight: "600" },
});

import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Image,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  TouchableOpacity,
  View,
} from "react-native";

import { ApiError, confirmDetection, discardDetection, getScan, retryDetection, searchCatalog } from "../api/client";
import type { CatalogSearchResult, Detection } from "../api/types";
import { alert } from "../lib/alert";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Review">;

export default function ReviewScreen({ route, navigation }: Props) {
  const { scanId, detectionId } = route.params;
  const [detection, setDetection] = useState<Detection | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [query, setQuery] = useState("");
  const [searchResults, setSearchResults] = useState<CatalogSearchResult[]>([]);
  const [customTitle, setCustomTitle] = useState("");
  const [customAuthor, setCustomAuthor] = useState("");

  useEffect(() => {
    (async () => {
      try {
        const scan = await getScan(scanId);
        const found = scan.detections.find((d) => d.id === detectionId) ?? null;
        setDetection(found);
        setCustomTitle(found?.read_title ?? "");
        setCustomAuthor(found?.read_author ?? "");
      } catch (err) {
        alert("Couldn't load this card", err instanceof ApiError ? err.message : "Unknown error");
      } finally {
        setLoading(false);
      }
    })();
  }, [scanId, detectionId]);

  useEffect(() => {
    const q = query.trim();
    if (q.length < 2) {
      setSearchResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      try {
        setSearchResults(await searchCatalog(q));
      } catch {
        // typeahead is best-effort; a failed search just shows no suggestions
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [query]);

  const goBack = () => navigation.goBack();

  const runAction = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      goBack();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Something went wrong.";
      alert("Action failed", message, [
        { text: "Cancel", style: "cancel" },
        { text: "Retry", onPress: () => runAction(action) },
      ]);
    } finally {
      setBusy(false);
    }
  };

  const handleRetryRead = async () => {
    setBusy(true);
    try {
      const updated = await retryDetection(detectionId);
      setDetection(updated);
      setCustomTitle(updated.read_title ?? "");
      setCustomAuthor(updated.read_author ?? "");
    } catch (err) {
      alert("Retry failed", err instanceof ApiError ? err.message : "Unknown error");
    } finally {
      setBusy(false);
    }
  };

  const candidates = useMemo(() => detection?.candidates ?? [], [detection]);

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#2f6690" />
      </View>
    );
  }

  if (!detection) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>This card is no longer available.</Text>
      </View>
    );
  }

  return (
    <ScrollView style={styles.container} contentContainerStyle={{ padding: 16 }}>
      <View style={styles.cropRow}>
        {detection.crop_url ? (
          <Image source={{ uri: detection.crop_url }} style={styles.crop} />
        ) : (
          <View style={[styles.crop, styles.cropPlaceholder]} />
        )}
        <View style={styles.cropInfo}>
          <Text style={styles.readTitle}>{detection.read_title || "Couldn't read this spine"}</Text>
          {!!detection.read_author && <Text style={styles.readAuthor}>{detection.read_author}</Text>}
          {detection.confidence != null && (
            <Text style={styles.confidence}>
              Confidence {(detection.confidence * 100).toFixed(0)}%
              {detection.margin != null ? ` · margin ${(detection.margin * 100).toFixed(0)}%` : ""}
            </Text>
          )}
        </View>
      </View>

      {detection.status === "failed" && (
        <TouchableOpacity style={styles.retryReadButton} onPress={handleRetryRead} disabled={busy}>
          <Text style={styles.retryReadText}>Retry Reading This Spine</Text>
        </TouchableOpacity>
      )}

      {candidates.length > 0 && (
        <View style={styles.block}>
          <Text style={styles.blockTitle}>Is it one of these?</Text>
          {candidates.map((c) => (
            <TouchableOpacity
              key={c.book_id}
              style={styles.candidateRow}
              disabled={busy}
              onPress={() => runAction(() => confirmDetection(detectionId, { book_id: c.book_id }))}
            >
              <View style={styles.rowText}>
                <Text style={styles.candidateTitle}>{c.title}</Text>
                <Text style={styles.candidateAuthor}>{c.author}</Text>
              </View>
              <Text style={styles.candidateScore}>{(c.score * 100).toFixed(0)}%</Text>
            </TouchableOpacity>
          ))}
        </View>
      )}

      <View style={styles.block}>
        <Text style={styles.blockTitle}>Search the catalog</Text>
        <TextInput
          style={styles.input}
          placeholder="Search by title or author"
          value={query}
          onChangeText={setQuery}
        />
        {searchResults.map((r) => (
          <TouchableOpacity
            key={r.id}
            style={styles.candidateRow}
            disabled={busy}
            onPress={() => runAction(() => confirmDetection(detectionId, { book_id: r.id }))}
          >
            <View style={styles.rowText}>
              <Text style={styles.candidateTitle}>{r.title}</Text>
              <Text style={styles.candidateAuthor}>{r.author}</Text>
            </View>
          </TouchableOpacity>
        ))}
      </View>

      <View style={styles.block}>
        <Text style={styles.blockTitle}>Or enter it manually</Text>
        <TextInput style={styles.input} placeholder="Title" value={customTitle} onChangeText={setCustomTitle} />
        <TextInput
          style={styles.input}
          placeholder="Author (optional)"
          value={customAuthor}
          onChangeText={setCustomAuthor}
        />
        <TouchableOpacity
          style={styles.primaryButton}
          disabled={busy || !customTitle.trim()}
          onPress={() =>
            runAction(() =>
              confirmDetection(detectionId, {
                custom_title: customTitle.trim(),
                custom_author: customAuthor.trim(),
              })
            )
          }
        >
          <Text style={styles.primaryButtonText}>Add This Book</Text>
        </TouchableOpacity>
      </View>

      <TouchableOpacity
        style={styles.discardButton}
        disabled={busy}
        onPress={() => runAction(() => discardDetection(detectionId))}
      >
        <Text style={styles.discardText}>Discard — not a book / duplicate</Text>
      </TouchableOpacity>

      {busy && <ActivityIndicator style={{ marginTop: 12 }} color="#2f6690" />}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24 },
  errorText: { fontSize: 16, color: "#8a3b3b" },
  cropRow: { flexDirection: "row", gap: 14 },
  crop: { width: 90, height: 130, borderRadius: 8, backgroundColor: "#eef3f7" },
  cropPlaceholder: {},
  cropInfo: { flex: 1, justifyContent: "center" },
  readTitle: { fontSize: 18, fontWeight: "700", color: "#1a2b3c" },
  readAuthor: { fontSize: 14, color: "#5b6b7a", marginTop: 4 },
  confidence: { fontSize: 12, color: "#9aa5b1", marginTop: 8 },
  retryReadButton: {
    marginTop: 16,
    paddingVertical: 12,
    borderRadius: 10,
    backgroundColor: "#fff2df",
    alignItems: "center",
  },
  retryReadText: { color: "#a15c00", fontWeight: "600" },
  block: { marginTop: 24 },
  blockTitle: { fontSize: 15, fontWeight: "700", color: "#1a2b3c", marginBottom: 10 },
  candidateRow: {
    flexDirection: "row",
    alignItems: "center",
    paddingVertical: 10,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#e4e9ee",
  },
  rowText: { flex: 1 },
  candidateTitle: { fontSize: 15, fontWeight: "600", color: "#1a2b3c" },
  candidateAuthor: { fontSize: 13, color: "#5b6b7a", marginTop: 2 },
  candidateScore: { fontSize: 13, color: "#2f6690", fontWeight: "600" },
  input: {
    borderWidth: 1,
    borderColor: "#dbe2e8",
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 15,
    marginBottom: 10,
  },
  primaryButton: { backgroundColor: "#2f6690", paddingVertical: 14, borderRadius: 10, alignItems: "center" },
  primaryButtonText: { color: "#fff", fontWeight: "600" },
  discardButton: { marginTop: 24, marginBottom: 40, alignItems: "center" },
  discardText: { color: "#8a3b3b", fontWeight: "500" },
});

import { useFocusEffect } from "@react-navigation/native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, StyleSheet, Text, TouchableOpacity, View } from "react-native";

import { ApiError, deleteLibraryEntry, getLibrary } from "../api/client";
import type { LibraryEntry } from "../api/types";
import { alert } from "../lib/alert";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Library">;

export default function LibraryScreen({ navigation }: Props) {
  const [entries, setEntries] = useState<LibraryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setEntries(await getLibrary());
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Couldn't load your library.");
    } finally {
      setLoading(false);
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load])
  );

  const handleDelete = (entry: LibraryEntry) => {
    alert("Remove book?", `Remove "${entry.title}" from your library?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Remove",
        style: "destructive",
        onPress: async () => {
          try {
            await deleteLibraryEntry(entry.id);
            setEntries((prev) => prev.filter((e) => e.id !== entry.id));
          } catch (err) {
            alert("Couldn't remove", err instanceof ApiError ? err.message : "Unknown error");
          }
        },
      },
    ]);
  };

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color="#2f6690" />
      </View>
    );
  }

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>{error}</Text>
        <TouchableOpacity style={styles.retryButton} onPress={load}>
          <Text style={styles.retryButtonText}>Retry</Text>
        </TouchableOpacity>
      </View>
    );
  }

  return (
    <FlatList
      style={styles.container}
      contentContainerStyle={entries.length === 0 && styles.centered}
      data={entries}
      keyExtractor={(item) => String(item.id)}
      refreshControl={<RefreshControl refreshing={false} onRefresh={load} />}
      ListEmptyComponent={
        <View>
          <Text style={styles.emptyTitle}>Your library is empty</Text>
          <Text style={styles.emptySubtitle}>Scan a shelf to start building it.</Text>
          <TouchableOpacity style={styles.retryButton} onPress={() => navigation.navigate("Scan")}>
            <Text style={styles.retryButtonText}>Scan a Shelf</Text>
          </TouchableOpacity>
        </View>
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.rowTitle}>{item.title}</Text>
            {!!item.author && <Text style={styles.rowAuthor}>{item.author}</Text>}
          </View>
          <TouchableOpacity onPress={() => handleDelete(item)} hitSlop={10}>
            <Text style={styles.deleteText}>Remove</Text>
          </TouchableOpacity>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: "#fff" },
  centered: { flex: 1, alignItems: "center", justifyContent: "center", padding: 24, gap: 12 },
  errorText: { fontSize: 16, color: "#8a3b3b", textAlign: "center" },
  emptyTitle: { fontSize: 18, fontWeight: "700", color: "#1a2b3c", textAlign: "center" },
  emptySubtitle: { fontSize: 14, color: "#5b6b7a", textAlign: "center", marginTop: 6, marginBottom: 16 },
  retryButton: { backgroundColor: "#2f6690", paddingHorizontal: 24, paddingVertical: 12, borderRadius: 10 },
  retryButtonText: { color: "#fff", fontWeight: "600" },
  row: {
    flexDirection: "row",
    alignItems: "center",
    paddingHorizontal: 16,
    paddingVertical: 14,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: "#e4e9ee",
  },
  rowText: { flex: 1 },
  rowTitle: { fontSize: 16, fontWeight: "600", color: "#1a2b3c" },
  rowAuthor: { fontSize: 13, color: "#5b6b7a", marginTop: 2 },
  deleteText: { color: "#8a3b3b", fontSize: 13, fontWeight: "500" },
});

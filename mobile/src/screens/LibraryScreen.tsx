/**
 * The library: every book the user has confirmed they own.
 *
 * This is the end of the flow, and the only screen that has a backend to talk
 * to in this phase. It reads /api/library/ and can remove an entry. Adding
 * happens through scanning, which does not exist yet -- hence the empty state
 * saying so rather than offering a button that goes nowhere.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { ApiError, API_BASE_URL, deleteLibraryEntry, getLibrary } from '../api/client';
import type { LibraryBook } from '../api/types';
import StatusBadge from '../components/StatusBadge';

type LoadState = 'loading' | 'ready' | 'error';

/**
 * React Native's Alert is a no-op under react-native-web, so the web build
 * would delete without ever asking. Each platform gets its own prompt.
 */
function confirmRemoval(title: string): Promise<boolean> {
  const message = `Remove "${title}" from your library?`;

  if (Platform.OS === 'web') {
    return Promise.resolve(
      typeof globalThis.confirm === 'function' ? globalThis.confirm(message) : true,
    );
  }

  return new Promise((resolve) => {
    Alert.alert('Remove book', message, [
      { text: 'Cancel', style: 'cancel', onPress: () => resolve(false) },
      { text: 'Remove', style: 'destructive', onPress: () => resolve(true) },
    ]);
  });
}

export default function LibraryScreen() {
  const [books, setBooks] = useState<LibraryBook[]>([]);
  const [loadState, setLoadState] = useState<LoadState>('loading');
  const [error, setError] = useState<ApiError | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [removingId, setRemovingId] = useState<number | null>(null);

  const load = useCallback(async () => {
    try {
      const page = await getLibrary();
      setBooks(page.results);
      setError(null);
      setLoadState('ready');
    } catch (cause) {
      setError(
        cause instanceof ApiError
          ? cause
          : new ApiError('Something went wrong loading your library.', 0),
      );
      setLoadState('error');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  const onRemove = useCallback(async (book: LibraryBook) => {
    if (!(await confirmRemoval(book.title))) return;

    setRemovingId(book.id);
    // Optimism would be wrong here: a failed delete that has already vanished
    // from the list reads as success. The row stays until the server agrees.
    try {
      await deleteLibraryEntry(book.id);
      setBooks((current) => current.filter((entry) => entry.id !== book.id));
    } catch (cause) {
      const message =
        cause instanceof ApiError ? cause.message : 'Could not remove that book.';
      if (Platform.OS === 'web') {
        setError(new ApiError(message, cause instanceof ApiError ? cause.status : 0));
      } else {
        Alert.alert('Could not remove', message);
      }
    } finally {
      setRemovingId(null);
    }
  }, []);

  if (loadState === 'loading') {
    return (
      <View style={styles.centered}>
        <ActivityIndicator />
      </View>
    );
  }

  if (loadState === 'error' && error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>
          {error.isNetworkError ? 'Cannot reach the server' : 'Something went wrong'}
        </Text>
        <Text style={styles.errorBody}>{error.message}</Text>
        {error.isNetworkError ? (
          <Text style={styles.errorHint}>
            Trying {API_BASE_URL}. On a physical device this must be your computer's
            LAN address, not localhost.
          </Text>
        ) : null}
        <Pressable
          style={({ pressed }) => [styles.retry, pressed && styles.pressed]}
          onPress={() => {
            setLoadState('loading');
            void load();
          }}
        >
          <Text style={styles.retryText}>Try again</Text>
        </Pressable>
      </View>
    );
  }

  return (
    <FlatList
      data={books}
      keyExtractor={(book) => String(book.id)}
      contentContainerStyle={books.length === 0 ? styles.emptyContainer : styles.list}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
      ListEmptyComponent={
        <View style={styles.empty}>
          <Text style={styles.emptyTitle}>No books yet</Text>
          <Text style={styles.emptyBody}>
            Scanning a shelf is not wired up yet. Once it is, confirmed books land
            here.
          </Text>
          <Text style={styles.emptyHint}>Pull down to refresh.</Text>
        </View>
      }
      renderItem={({ item }) => (
        <View style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.title} numberOfLines={2}>
              {item.title}
            </Text>
            {item.author ? (
              <Text style={styles.author} numberOfLines={1}>
                {item.author}
                {item.catalog_book?.year ? ` · ${item.catalog_book.year}` : ''}
              </Text>
            ) : null}
            <View style={styles.badges}>
              <StatusBadge status={item.source} />
              {item.catalog_book === null ? (
                <Text style={styles.uncatalogued}>Not in catalog</Text>
              ) : null}
            </View>
          </View>
          <Pressable
            accessibilityLabel={`Remove ${item.title}`}
            disabled={removingId === item.id}
            onPress={() => void onRemove(item)}
            style={({ pressed }) => [styles.remove, pressed && styles.pressed]}
          >
            {removingId === item.id ? (
              <ActivityIndicator size="small" />
            ) : (
              <Text style={styles.removeText}>Remove</Text>
            )}
          </Pressable>
        </View>
      )}
    />
  );
}

const styles = StyleSheet.create({
  centered: {
    alignItems: 'center',
    flex: 1,
    justifyContent: 'center',
    padding: 32,
  },
  list: {
    padding: 16,
  },
  emptyContainer: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 32,
  },
  empty: {
    alignItems: 'center',
  },
  emptyTitle: {
    color: '#1B1F27',
    fontSize: 20,
    fontWeight: '600',
    marginBottom: 8,
  },
  emptyBody: {
    color: '#5B6272',
    fontSize: 15,
    lineHeight: 21,
    marginBottom: 12,
    maxWidth: 320,
    textAlign: 'center',
  },
  emptyHint: {
    color: '#8C93A1',
    fontSize: 13,
  },
  errorTitle: {
    color: '#1B1F27',
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 8,
  },
  errorBody: {
    color: '#5B6272',
    fontSize: 15,
    marginBottom: 8,
    maxWidth: 340,
    textAlign: 'center',
  },
  errorHint: {
    color: '#8C93A1',
    fontSize: 13,
    marginBottom: 16,
    maxWidth: 340,
    textAlign: 'center',
  },
  retry: {
    backgroundColor: '#1B1F27',
    borderRadius: 8,
    paddingHorizontal: 20,
    paddingVertical: 10,
  },
  retryText: {
    color: '#FFFFFF',
    fontSize: 15,
    fontWeight: '600',
  },
  row: {
    alignItems: 'center',
    borderBottomColor: '#ECEEF2',
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: 'row',
    gap: 12,
    paddingVertical: 14,
  },
  rowText: {
    flex: 1,
    gap: 4,
  },
  title: {
    color: '#1B1F27',
    fontSize: 16,
    fontWeight: '600',
  },
  author: {
    color: '#5B6272',
    fontSize: 14,
  },
  badges: {
    alignItems: 'center',
    flexDirection: 'row',
    gap: 8,
    marginTop: 2,
  },
  uncatalogued: {
    color: '#8C93A1',
    fontSize: 12,
  },
  remove: {
    minWidth: 68,
    paddingHorizontal: 8,
    paddingVertical: 6,
  },
  removeText: {
    color: '#9B2C2C',
    fontSize: 14,
    fontWeight: '600',
    textAlign: 'right',
  },
  pressed: {
    opacity: 0.6,
  },
});

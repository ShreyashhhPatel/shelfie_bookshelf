/**
 * What one scan found.
 *
 * Shows the photo, then a row per spine: the crop the detector cut, what the
 * model read off it, and where the matcher landed. Confirming and correcting
 * is the review phase; this screen's job is to make the pipeline's output
 * legible, including where it was unsure and why.
 *
 * The scan is passed through navigation when arriving from a fresh upload --
 * it is already in hand, and refetching it would be a wasted round trip -- and
 * fetched by id otherwise.
 */

import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Image,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { ApiError, getScan } from '../api/client';
import type { Detection, Scan } from '../api/types';
import StatusBadge from '../components/StatusBadge';
import type { RootStackScreenProps } from '../navigation/types';

function DetectionRow({ detection }: { detection: Detection }) {
  const top = detection.candidates[0];
  const unread = !detection.raw_title.trim();

  return (
    <View style={styles.row}>
      {detection.crop_url ? (
        <Image
          source={{ uri: detection.crop_url }}
          style={styles.thumb}
          resizeMode="cover"
        />
      ) : (
        <View style={[styles.thumb, styles.thumbEmpty]} />
      )}

      <View style={styles.rowText}>
        {unread ? (
          <Text style={styles.unread}>Nothing legible on this spine</Text>
        ) : (
          <>
            <Text style={styles.read} numberOfLines={2}>
              {detection.raw_title}
            </Text>
            {detection.raw_author ? (
              <Text style={styles.readAuthor} numberOfLines={1}>
                {detection.raw_author}
              </Text>
            ) : null}
          </>
        )}

        {top ? (
          <Text style={styles.candidate} numberOfLines={1}>
            → {top.title}
            {/* Margin, not score, is what decided this row's status, so it is
                the number worth showing next to it. */}
            <Text style={styles.scores}>
              {'  '}
              {top.score.toFixed(2)} · margin {detection.margin.toFixed(2)}
            </Text>
          </Text>
        ) : !unread ? (
          <Text style={styles.noMatch}>No catalog match</Text>
        ) : null}

        <View style={styles.badges}>
          <StatusBadge status={detection.status} />
        </View>
      </View>
    </View>
  );
}

export default function ResultsScreen({ route }: RootStackScreenProps<'Results'>) {
  const { scanId, scan: passed } = route.params;

  const [scan, setScan] = useState<Scan | null>(passed ?? null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setScan(await getScan(scanId));
      setError(null);
    } catch (cause) {
      setError(
        cause instanceof ApiError ? cause.message : 'Could not load this scan.',
      );
    }
  }, [scanId]);

  useEffect(() => {
    if (!passed) void load();
  }, [passed, load]);

  if (error) {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  if (!scan) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator />
      </View>
    );
  }

  if (scan.status === 'failed') {
    return (
      <View style={styles.centered}>
        <Text style={styles.errorTitle}>
          {scan.error_code === 'rate_limited' ? 'Too many scans' : 'Scan failed'}
        </Text>
        <Text style={styles.errorText}>{scan.error || 'No detail was recorded.'}</Text>
        <Text style={styles.errorHint}>
          {scan.is_retryable
            ? 'This usually clears on its own. Scan the shelf again in a moment.'
            : 'This needs fixing on the server before scanning will work.'}
        </Text>
      </View>
    );
  }

  const stages = Object.entries(scan.timings);

  return (
    <FlatList
      data={scan.detections}
      keyExtractor={(detection) => String(detection.id)}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <View>
          {scan.image_url ? (
            <Image
              source={{ uri: scan.image_url }}
              style={styles.hero}
              resizeMode="cover"
            />
          ) : null}

          <View style={styles.summary}>
            <Text style={styles.summaryTitle}>
              {scan.counts.total} {scan.counts.total === 1 ? 'spine' : 'spines'} found
            </Text>
            <Text style={styles.summaryBody}>
              {scan.counts.auto_matched} matched confidently ·{' '}
              {scan.counts.needs_review} need review
            </Text>
            {stages.length ? (
              <Text style={styles.timings}>
                {stages.map(([name, ms]) => `${name} ${ms}ms`).join('  ·  ')}
              </Text>
            ) : null}
          </View>
        </View>
      }
      ListEmptyComponent={
        <View style={styles.centered}>
          <Text style={styles.emptyTitle}>No spines found</Text>
          <Text style={styles.errorText}>
            Try a straighter, closer photo of a single shelf.
          </Text>
        </View>
      }
      renderItem={({ item }) => <DetectionRow detection={item} />}
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
    paddingBottom: 24,
  },
  hero: {
    aspectRatio: 3 / 2,
    width: '100%',
  },
  summary: {
    borderBottomColor: '#ECEEF2',
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 4,
    padding: 16,
  },
  summaryTitle: {
    color: '#1B1F27',
    fontSize: 18,
    fontWeight: '700',
  },
  summaryBody: {
    color: '#5B6272',
    fontSize: 14,
  },
  timings: {
    color: '#A0A6B2',
    fontSize: 11,
    marginTop: 4,
  },
  row: {
    borderBottomColor: '#F2F4F7',
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: 'row',
    gap: 12,
    paddingHorizontal: 16,
    paddingVertical: 12,
  },
  thumb: {
    backgroundColor: '#F2F4F7',
    borderRadius: 6,
    height: 56,
    width: 84,
  },
  thumbEmpty: {
    borderColor: '#E4E7EC',
    borderWidth: StyleSheet.hairlineWidth,
  },
  rowText: {
    flex: 1,
    gap: 3,
  },
  read: {
    color: '#1B1F27',
    fontSize: 15,
    fontWeight: '600',
  },
  readAuthor: {
    color: '#5B6272',
    fontSize: 13,
  },
  unread: {
    color: '#8C93A1',
    fontSize: 15,
    fontStyle: 'italic',
  },
  candidate: {
    color: '#2A5DA8',
    fontSize: 13,
  },
  scores: {
    color: '#A0A6B2',
    fontSize: 11,
  },
  noMatch: {
    color: '#8C93A1',
    fontSize: 13,
  },
  badges: {
    flexDirection: 'row',
    gap: 8,
    marginTop: 4,
  },
  emptyTitle: {
    color: '#1B1F27',
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 6,
  },
  errorTitle: {
    color: '#1B1F27',
    fontSize: 18,
    fontWeight: '600',
    marginBottom: 6,
  },
  errorText: {
    color: '#5B6272',
    fontSize: 14,
    textAlign: 'center',
  },
  errorHint: {
    color: '#8C93A1',
    fontSize: 13,
    marginTop: 10,
    maxWidth: 320,
    textAlign: 'center',
  },
});

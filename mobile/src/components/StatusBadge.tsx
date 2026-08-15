/**
 * A small pill for any state the backend can report.
 *
 * One component covers every status union so that a state means the same thing
 * visually wherever it appears -- `needs_review` should not be amber on the
 * review screen and grey in a list. The map below is exhaustive over the
 * unions in api/types.ts; adding a backend state without adding it here is a
 * type error rather than a blank badge.
 */

import { StyleSheet, Text, View } from 'react-native';

import type { DetectionStatus, LibrarySource, ScanStatus } from '../api/types';

export type BadgeStatus = ScanStatus | DetectionStatus | LibrarySource;

type Tone = 'neutral' | 'info' | 'warn' | 'good' | 'bad';

const TONES: Record<Tone, { bg: string; fg: string }> = {
  neutral: { bg: '#EEF0F3', fg: '#565D6B' },
  info: { bg: '#E4EDFB', fg: '#2A5DA8' },
  warn: { bg: '#FDF0DC', fg: '#8A5A12' },
  good: { bg: '#E2F2E7', fg: '#276A3C' },
  bad: { bg: '#FBE6E6', fg: '#9B2C2C' },
};

const STATUSES: Record<BadgeStatus, { label: string; tone: Tone }> = {
  // Scan lifecycle. Everything mid-pipeline reads as "working".
  pending: { label: 'Pending', tone: 'neutral' },
  detecting: { label: 'Finding spines', tone: 'info' },
  reading: { label: 'Reading spines', tone: 'info' },
  matching: { label: 'Matching', tone: 'info' },
  complete: { label: 'Complete', tone: 'good' },
  failed: { label: 'Failed', tone: 'bad' },

  // Detection outcomes. `needs_review` is warn rather than bad: it is the
  // expected path for an ambiguous spine, not an error.
  auto_matched: { label: 'Matched', tone: 'good' },
  needs_review: { label: 'Needs review', tone: 'warn' },
  confirmed: { label: 'Confirmed', tone: 'good' },
  discarded: { label: 'Discarded', tone: 'neutral' },

  // How a library book got there.
  scan: { label: 'Scanned', tone: 'info' },
  manual: { label: 'Added by hand', tone: 'neutral' },
};

interface Props {
  status: BadgeStatus;
}

export default function StatusBadge({ status }: Props) {
  const { label, tone } = STATUSES[status];
  const { bg, fg } = TONES[tone];

  return (
    <View style={[styles.badge, { backgroundColor: bg }]}>
      <Text style={[styles.text, { color: fg }]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  badge: {
    alignSelf: 'flex-start',
    borderRadius: 999,
    paddingHorizontal: 8,
    paddingVertical: 3,
  },
  text: {
    fontSize: 11,
    fontWeight: '600',
    letterSpacing: 0.2,
  },
});

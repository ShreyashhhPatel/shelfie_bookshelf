/**
 * Take or pick a shelf photo, normalize it, and send it to be scanned.
 *
 * The one non-obvious step is the JPEG conversion. iPhones shoot HEIC by
 * default, and Pillow and the CV stack behind the detector frequently cannot
 * open it -- the upload succeeds and the scan fails server-side with something
 * unhelpful. Converting on the device turns that into a non-issue.
 *
 * Deliberately no resize. Downscaling is exactly what destroys the small type
 * on a spine, which is the text the whole product depends on reading.
 */

import { useCallback, useState } from 'react';
import {
  ActivityIndicator,
  Image,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import * as ImageManipulator from 'expo-image-manipulator';
import { SaveFormat } from 'expo-image-manipulator';
import * as ImagePicker from 'expo-image-picker';

import { ApiError, uploadScan } from '../api/client';
import { notify } from '../lib/alert';
import type { Scan } from '../api/types';
import type { RootStackScreenProps } from '../navigation/types';

type Source = 'camera' | 'library';

/**
 * Re-encode to JPEG with no transformations.
 *
 * `manipulate(...).renderAsync().saveAsync(...)` is the SDK 57 API;
 * `manipulateAsync` still exists but is deprecated. No `.resize()` call in the
 * chain is the point, not an omission.
 */
async function toJpeg(uri: string): Promise<string> {
  const context = ImageManipulator.ImageManipulator.manipulate(uri);
  const rendered = await context.renderAsync();
  const result = await rendered.saveAsync({ format: SaveFormat.JPEG });
  return result.uri;
}

export default function ScanScreen({ navigation }: RootStackScreenProps<'Scan'>) {
  const [busy, setBusy] = useState<Source | 'uploading' | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  /**
   * A pipeline failure comes back as a *successful* 201 whose Scan carries
   * status 'failed' -- the resource was created, the work inside it did not
   * finish. Holding it here shows the reason on this screen with a retry,
   * rather than pushing the user to a Results screen with nothing on it.
   */
  const [failed, setFailed] = useState<Scan | null>(null);
  /** The converted JPEG, kept so retry re-sends it without re-picking. */
  const [pendingUri, setPendingUri] = useState<string | null>(null);

  const send = useCallback(
    async (uri: string, alreadyJpeg = false) => {
      setBusy('uploading');
      setFailed(null);
      try {
        const jpeg = alreadyJpeg ? uri : await toJpeg(uri);
        setPreview(jpeg);
        setPendingUri(jpeg);

        const scan = await uploadScan(jpeg);

        if (scan.status === 'failed') {
          setFailed(scan);
          return;
        }

        // `replace`, not `navigate`: going back to a spent upload screen is
        // never what the user wants after a scan lands.
        navigation.replace('Results', { scanId: scan.id, scan });
      } catch (cause) {
        // A transport failure, as opposed to a pipeline failure above. The
        // server was never reached, so there is no Scan to report.
        const message =
          cause instanceof ApiError
            ? cause.message
            : 'Could not scan that photo. Try again.';
        notify('Upload failed', message);
      } finally {
        setBusy(null);
      }
    },
    [navigation],
  );

  const retry = useCallback(() => {
    if (pendingUri) void send(pendingUri, true);
  }, [pendingUri, send]);

  const pick = useCallback(
    async (source: Source) => {
      setBusy(source);
      try {
        const permission =
          source === 'camera'
            ? await ImagePicker.requestCameraPermissionsAsync()
            : await ImagePicker.requestMediaLibraryPermissionsAsync();

        if (!permission.granted) {
          notify(
            'Permission needed',
            source === 'camera'
              ? 'Shelfie needs camera access to photograph a shelf.'
              : 'Shelfie needs photo access to pick a shelf photo.',
          );
          return;
        }

        const options: ImagePicker.ImagePickerOptions = {
          mediaTypes: ['images'],
          // Full quality out of the picker; the conversion below is where the
          // format is decided, and nothing resizes along the way.
          quality: 1,
        };

        const result =
          source === 'camera'
            ? await ImagePicker.launchCameraAsync(options)
            : await ImagePicker.launchImageLibraryAsync(options);

        if (result.canceled || !result.assets?.length) return;

        await send(result.assets[0].uri);
      } finally {
        setBusy((current) => (current === source ? null : current));
      }
    },
    [send],
  );

  const uploading = busy === 'uploading';

  return (
    <ScrollView contentContainerStyle={styles.container}>
      <Text style={styles.title}>Scan a shelf</Text>
      <Text style={styles.body}>
        Photograph one shelf straight on, close enough that the titles are legible
        to you. Every spine is read in a single pass.
      </Text>

      {preview ? (
        <Image source={{ uri: preview }} style={styles.preview} resizeMode="cover" />
      ) : null}

      {failed ? (
        <View style={styles.failure}>
          <Text style={styles.failureTitle}>
            {failed.error_code === 'rate_limited'
              ? 'Too many scans'
              : "Couldn't read this shelf"}
          </Text>
          {/* Server-authored and already user-facing: the raw provider payload
              stays in the backend log. */}
          <Text style={styles.failureBody}>{failed.error}</Text>

          {failed.is_retryable ? (
            <Pressable
              style={({ pressed }) => [styles.button, pressed && styles.pressed]}
              onPress={retry}
            >
              <Text style={styles.buttonText}>Try again</Text>
            </Pressable>
          ) : (
            <Text style={styles.failureHint}>
              Retrying will not help until this is fixed on the server.
            </Text>
          )}

          <Pressable
            style={({ pressed }) => [
              styles.button,
              styles.secondary,
              pressed && styles.pressed,
            ]}
            onPress={() => {
              setFailed(null);
              setPreview(null);
              setPendingUri(null);
            }}
          >
            <Text style={[styles.buttonText, styles.secondaryText]}>
              Use a different photo
            </Text>
          </Pressable>
        </View>
      ) : uploading ? (
        <View style={styles.busy}>
          <ActivityIndicator />
          <Text style={styles.busyText}>Detecting spines and reading them…</Text>
          <Text style={styles.busyHint}>
            This takes a few seconds. The whole shelf goes to the model in one
            request.
          </Text>
        </View>
      ) : (
        <View style={styles.actions}>
          {/* The simulator has no camera, so the button would silently do
              nothing there. Saying so beats an unexplained dead control. */}
          <Pressable
            style={({ pressed }) => [styles.button, pressed && styles.pressed]}
            disabled={busy !== null}
            onPress={() => void pick('camera')}
          >
            <Text style={styles.buttonText}>Take a photo</Text>
          </Pressable>

          <Pressable
            style={({ pressed }) => [
              styles.button,
              styles.secondary,
              pressed && styles.pressed,
            ]}
            disabled={busy !== null}
            onPress={() => void pick('library')}
          >
            <Text style={[styles.buttonText, styles.secondaryText]}>
              Choose from library
            </Text>
          </Pressable>

          {Platform.OS !== 'web' ? null : (
            <Text style={styles.note}>
              On web the camera option opens a file picker.
            </Text>
          )}
        </View>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  container: {
    flexGrow: 1,
    justifyContent: 'center',
    padding: 24,
  },
  title: {
    color: '#1B1F27',
    fontSize: 24,
    fontWeight: '700',
    marginBottom: 8,
  },
  body: {
    color: '#5B6272',
    fontSize: 15,
    lineHeight: 22,
    marginBottom: 24,
  },
  preview: {
    aspectRatio: 4 / 3,
    borderRadius: 12,
    marginBottom: 24,
    width: '100%',
  },
  actions: {
    gap: 12,
  },
  button: {
    alignItems: 'center',
    backgroundColor: '#1B1F27',
    borderRadius: 10,
    paddingVertical: 15,
  },
  secondary: {
    backgroundColor: '#EEF0F3',
  },
  buttonText: {
    color: '#FFFFFF',
    fontSize: 16,
    fontWeight: '600',
  },
  secondaryText: {
    color: '#1B1F27',
  },
  pressed: {
    opacity: 0.7,
  },
  busy: {
    alignItems: 'center',
    gap: 10,
    paddingVertical: 24,
  },
  busyText: {
    color: '#1B1F27',
    fontSize: 15,
    fontWeight: '600',
  },
  busyHint: {
    color: '#8C93A1',
    fontSize: 13,
    maxWidth: 300,
    textAlign: 'center',
  },
  note: {
    color: '#8C93A1',
    fontSize: 13,
    marginTop: 4,
    textAlign: 'center',
  },
  failure: {
    gap: 12,
  },
  failureTitle: {
    color: '#9B2C2C',
    fontSize: 18,
    fontWeight: '700',
  },
  failureBody: {
    color: '#5B6272',
    fontSize: 15,
    lineHeight: 22,
  },
  failureHint: {
    color: '#8C93A1',
    fontSize: 13,
  },
});

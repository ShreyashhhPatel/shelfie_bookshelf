import * as ImageManipulator from "expo-image-manipulator";
import * as ImagePicker from "expo-image-picker";
import { useState } from "react";
import { ActivityIndicator, Platform, StyleSheet, Text, TouchableOpacity, View } from "react-native";
import type { NativeStackScreenProps } from "@react-navigation/native-stack";

import { ApiError, uploadScan } from "../api/client";
import type { UploadImage } from "../api/client";
import { alert } from "../lib/alert";
import type { RootStackParamList } from "../navigation/types";

type Props = NativeStackScreenProps<RootStackParamList, "Scan">;

// Force a high-quality JPEG re-encode on-device: iPhones shoot HEIC, which
// the CV libraries on the server often can't open, and this also acts as
// the first of two safety nets (the server re-converts too). Deliberately
// no resize -- downscaling is what blurs small spine text.
async function toUploadableJpeg(uri: string): Promise<string> {
  const result = await ImageManipulator.manipulateAsync(uri, [], {
    compress: 0.95,
    format: ImageManipulator.SaveFormat.JPEG,
  });
  return result.uri;
}

export default function ScanScreen({ navigation }: Props) {
  const [uploading, setUploading] = useState(false);

  const handleImage = async (asset: ImagePicker.ImagePickerAsset) => {
    setUploading(true);
    try {
      // Skip the on-device re-encode in the browser: it runs through a
      // canvas, and Chrome can't decode HEIC, so an iPhone photo threw here
      // before any request was sent. Upload the picked file as-is -- the
      // server converts it anyway (pillow-heif, see image_utils.py).
      const image: UploadImage =
        Platform.OS === "web"
          ? { uri: asset.uri, file: asset.file, name: asset.fileName ?? undefined }
          : { uri: await toUploadableJpeg(asset.uri) };

      const scan = await uploadScan(image);
      navigation.navigate("Results", { scanId: scan.id });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : "Something went wrong preparing that photo.";
      alert("Upload failed", message, [
        { text: "Cancel", style: "cancel" },
        { text: "Retry", onPress: () => handleImage(asset) },
      ]);
    } finally {
      setUploading(false);
    }
  };


  const takePhoto = async () => {
    const permission = await ImagePicker.requestCameraPermissionsAsync();
    if (!permission.granted) {
      alert("Camera access needed", "Enable camera access in Settings to scan a shelf.");
      return;
    }
    const result = await ImagePicker.launchCameraAsync({ quality: 1 });
    if (!result.canceled && result.assets[0]) {
      handleImage(result.assets[0]);
    }
  };

  const pickPhoto = async () => {
    const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
    if (!permission.granted) {
      alert("Photo library access needed", "Enable photo access in Settings to choose a shelf photo.");
      return;
    }
    const result = await ImagePicker.launchImageLibraryAsync({ quality: 1 });
    if (!result.canceled && result.assets[0]) {
      handleImage(result.assets[0]);
    }
  };

  return (
    <View style={styles.container}>
      <Text style={styles.title}>Shelfie</Text>
      <Text style={styles.subtitle}>Photograph a bookshelf and we'll build your library.</Text>

      {uploading ? (
        <View style={styles.uploadingBox}>
          <ActivityIndicator size="large" color="#2f6690" />
          <Text style={styles.uploadingText}>Reading your shelf…</Text>
        </View>
      ) : (
        <View style={styles.actions}>
          <TouchableOpacity style={styles.primaryButton} onPress={takePhoto}>
            <Text style={styles.primaryButtonText}>Take Photo</Text>
          </TouchableOpacity>
          <TouchableOpacity style={styles.secondaryButton} onPress={pickPhoto}>
            <Text style={styles.secondaryButtonText}>Choose from Library</Text>
          </TouchableOpacity>
        </View>
      )}

      <TouchableOpacity style={styles.libraryLink} onPress={() => navigation.navigate("Library")}>
        <Text style={styles.libraryLinkText}>View My Library</Text>
      </TouchableOpacity>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: "#fff",
    alignItems: "center",
    justifyContent: "center",
    padding: 24,
  },
  title: { fontSize: 32, fontWeight: "700", color: "#1a2b3c" },
  subtitle: {
    fontSize: 15,
    color: "#5b6b7a",
    textAlign: "center",
    marginTop: 8,
    marginBottom: 40,
  },
  actions: { width: "100%", gap: 12 },
  primaryButton: {
    backgroundColor: "#2f6690",
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  primaryButtonText: { color: "#fff", fontSize: 17, fontWeight: "600" },
  secondaryButton: {
    backgroundColor: "#eef3f7",
    paddingVertical: 16,
    borderRadius: 12,
    alignItems: "center",
  },
  secondaryButtonText: { color: "#2f6690", fontSize: 17, fontWeight: "600" },
  uploadingBox: { alignItems: "center", gap: 12, minHeight: 140, justifyContent: "center" },
  uploadingText: { color: "#5b6b7a", fontSize: 15 },
  libraryLink: { marginTop: 32, padding: 8 },
  libraryLinkText: { color: "#2f6690", fontSize: 15, fontWeight: "500" },
});

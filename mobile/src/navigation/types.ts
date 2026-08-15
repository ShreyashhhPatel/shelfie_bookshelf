export type RootStackParamList = {
  Scan: undefined;
  Results: { scanId: number };
  Review: { scanId: number; detectionId: number };
  Library: undefined;
};

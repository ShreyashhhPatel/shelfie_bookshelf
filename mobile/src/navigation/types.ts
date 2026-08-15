/**
 * The navigation graph, typed.
 *
 * Only Library exists in this phase. Scan, Review, and BookDetail get added
 * here as the phases that build them land, and each addition immediately
 * type-checks every navigate() call in the app.
 */

import type { NativeStackScreenProps } from '@react-navigation/native-stack';

export type RootStackParamList = {
  Library: undefined;
};

export type RootStackScreenProps<T extends keyof RootStackParamList> =
  NativeStackScreenProps<RootStackParamList, T>;

/**
 * Makes `useNavigation()` typed by default anywhere in the tree, so screens do
 * not each have to re-declare the param list.
 */
declare global {
  // eslint-disable-next-line @typescript-eslint/no-namespace
  namespace ReactNavigation {
    interface RootParamList extends RootStackParamList {}
  }
}

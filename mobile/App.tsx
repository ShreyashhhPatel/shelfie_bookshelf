import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { StatusBar } from 'expo-status-bar';
import { Pressable, Text, StyleSheet } from 'react-native';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import LibraryScreen from './src/screens/LibraryScreen';
import ResultsScreen from './src/screens/ResultsScreen';
import ScanScreen from './src/screens/ScanScreen';
import type { RootStackParamList } from './src/navigation/types';

const Stack = createNativeStackNavigator<RootStackParamList>();

export default function App() {
  return (
    <SafeAreaProvider>
      <NavigationContainer>
        <Stack.Navigator
          screenOptions={{
            headerStyle: { backgroundColor: '#FFFFFF' },
            headerTitleStyle: { fontWeight: '600' },
            headerShadowVisible: false,
            contentStyle: { backgroundColor: '#FFFFFF' },
          }}
        >
          <Stack.Screen
            name="Library"
            component={LibraryScreen}
            options={({ navigation }) => ({
              title: 'My Library',
              headerRight: () => (
                <Pressable
                  accessibilityLabel="Scan a shelf"
                  onPress={() => navigation.navigate('Scan')}
                  style={({ pressed }) => pressed && styles.pressed}
                >
                  <Text style={styles.headerAction}>Scan</Text>
                </Pressable>
              ),
            })}
          />
          <Stack.Screen
            name="Scan"
            component={ScanScreen}
            options={{ title: 'New scan' }}
          />
          <Stack.Screen
            name="Results"
            component={ResultsScreen}
            options={{ title: 'Scan results' }}
          />
        </Stack.Navigator>
      </NavigationContainer>
      <StatusBar style="auto" />
    </SafeAreaProvider>
  );
}

const styles = StyleSheet.create({
  headerAction: {
    color: '#2A5DA8',
    fontSize: 16,
    fontWeight: '600',
  },
  pressed: {
    opacity: 0.6,
  },
});

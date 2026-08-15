import { NavigationContainer } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { StatusBar } from "expo-status-bar";
import { TouchableOpacity, Text } from "react-native";

import LibraryScreen from "./src/screens/LibraryScreen";
import ResultsScreen from "./src/screens/ResultsScreen";
import ReviewScreen from "./src/screens/ReviewScreen";
import ScanScreen from "./src/screens/ScanScreen";
import type { RootStackParamList } from "./src/navigation/types";

const Stack = createNativeStackNavigator<RootStackParamList>();

const linking = {
  prefixes: [],
  config: {
    screens: {
      Scan: "",
      Results: "results/:scanId",
      Review: "review/:scanId/:detectionId",
      Library: "library",
    },
  },
};

export default function App() {
  return (
    <NavigationContainer linking={linking}>
      <StatusBar style="dark" />
      <Stack.Navigator screenOptions={{ headerTintColor: "#2f6690" }}>
        <Stack.Screen name="Scan" component={ScanScreen} options={{ title: "Shelfie", headerShown: false }} />
        <Stack.Screen name="Results" component={ResultsScreen} options={{ title: "Scan Results" }} />
        <Stack.Screen name="Review" component={ReviewScreen} options={{ title: "Review" }} />
        <Stack.Screen
          name="Library"
          component={LibraryScreen}
          options={({ navigation }) => ({
            title: "My Library",
            headerRight: () => (
              <TouchableOpacity onPress={() => navigation.navigate("Scan")}>
                <Text style={{ color: "#2f6690", fontWeight: "600" }}>Scan</Text>
              </TouchableOpacity>
            ),
          })}
        />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

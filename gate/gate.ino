#include <WiFi.h>
#include <FirebaseESP32.h>
#include <Preferences.h>  // For saving the last booking ID in non-volatile memory

// GPIO pin for gate control
#define GATE_PIN 23

// WiFi Credentials
#define WIFI_SSID "OPPO"
#define WIFI_PASSWORD "12345678"

// Firebase Credentials
#define FIREBASE_HOST "cityparkpro-default-rtdb.europe-west1.firebasedatabase.app"
#define FIREBASE_AUTH "AIzaSyDbB2Elr8A20f1cnDPrzpdZleXT3dvK5PM"

// Create Firebase objects
FirebaseData fbdo;
FirebaseAuth auth;
FirebaseConfig config;

// Create a preferences object for persistent storage
Preferences preferences;

unsigned long unlockStartTime = 0;
bool gateUnlocked = false;
String lastProcessedBookingID = "";  // To track what booking we've processed

void setup() {
  Serial.begin(115200);
  Serial.println("\n\n====== CityParkPro Gate Controller ======");
  
  pinMode(GATE_PIN, OUTPUT);
  digitalWrite(GATE_PIN, LOW);  // Initialize gate as locked
  
  // Connect to WiFi
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    Serial.print(".");
    delay(300);
  }
  Serial.println();
  Serial.print("Connected to WiFi, IP: ");
  Serial.println(WiFi.localIP());
  
  // Initialize Firebase
  Serial.println("Connecting to Firebase...");
  config.host = FIREBASE_HOST;
  config.signer.tokens.legacy_token = FIREBASE_AUTH;
  
  Firebase.begin(&config, &auth);
  Firebase.reconnectWiFi(true);
  
  // Set database read timeout
  Firebase.setReadTimeout(fbdo, 1000 * 60);
  // Set write size limit
  Firebase.setwriteSizeLimit(fbdo, "tiny");
  
  // Initial connection test
  if (Firebase.ready()) {
    Serial.println("Firebase connection successful!");
  } else {
    Serial.println("Firebase connection failed.");
    Serial.println("Reason: " + fbdo.errorReason());
  }
}

void loop() {
  // Try to read the gate control data
  if (Firebase.getJSON(fbdo, "/gateControl")) {
    Serial.println("Firebase data retrieved successfully");
    
    // Get the complete JSON data as a string
    String jsonStr;
    fbdo.jsonObject().toString(jsonStr, true);
    Serial.println("Received JSON data:");
    Serial.println(jsonStr);
    
    // Parse the JSON string manually
    // First, find the booking ID
    int firstBracePos = jsonStr.indexOf('{', 1);  // Skip the first opening brace
    int firstColonPos = jsonStr.indexOf(':', firstBracePos);
    
    if (firstBracePos > 0 && firstColonPos > firstBracePos) {
      // Extract the bookingID
      String bookingID = jsonStr.substring(firstBracePos + 1, firstColonPos);
      bookingID.trim();
      // Remove any quotes
      bookingID.replace("\"", "");
      bookingID.replace("'", "");
      
      Serial.println("Found booking ID: " + bookingID);
      
      // Now extract action, duration and timestamp from the string
      // Find action
      String actionKey = "\"action\":";
      int actionPos = jsonStr.indexOf(actionKey);
      if (actionPos > 0) {
        int actionValueStart = actionPos + actionKey.length();
        int actionValueEnd = jsonStr.indexOf(',', actionValueStart);
        if (actionValueEnd < 0) { // If no comma, maybe it's the last field
          actionValueEnd = jsonStr.indexOf('}', actionValueStart);
        }
        
        String action = jsonStr.substring(actionValueStart, actionValueEnd);
        action.trim();
        action.replace("\"", ""); // Remove quotes
        Serial.println("  Action: " + action);
        
        // Find duration
        String durationKey = "\"duration\":";
        int durationPos = jsonStr.indexOf(durationKey);
        if (durationPos > 0) {
          int durationValueStart = durationPos + durationKey.length();
          int durationValueEnd = jsonStr.indexOf(',', durationValueStart);
          if (durationValueEnd < 0) {
            durationValueEnd = jsonStr.indexOf('}', durationValueStart);
          }
          
          String durationStr = jsonStr.substring(durationValueStart, durationValueEnd);
          durationStr.trim();
          int duration = durationStr.toInt();
          Serial.println("  Duration: " + String(duration));
          
          // Find timestamp
          String timestampKey = "\"timestamp\":";
          int timestampPos = jsonStr.indexOf(timestampKey);
          if (timestampPos > 0) {
            int timestampValueStart = timestampPos + timestampKey.length();
            int timestampValueEnd = jsonStr.indexOf(',', timestampValueStart);
            if (timestampValueEnd < 0) {
              timestampValueEnd = jsonStr.indexOf('}', timestampValueStart);
            }
            
            String timestamp = jsonStr.substring(timestampValueStart, timestampValueEnd);
            timestamp.trim();
            timestamp.replace("\"", ""); // Remove quotes
            Serial.println("  Timestamp: " + timestamp);
            
            // Check if this is a booking command we need to process
            if (action == "unlock") {
              // Always process the current unlock command after reset
              if (bookingID != lastProcessedBookingID) {
                unlockGate(bookingID, duration);
                lastProcessedBookingID = bookingID;  // Remember we processed this booking
              } else {
                Serial.println("Already processed this booking ID: " + bookingID);
              }
            }
          }
        }
      }
    }
  } else {
    Serial.println("Failed to get data");
    Serial.println("Reason: " + fbdo.errorReason());
  }
  
  // Check if gate needs to be locked
  if (gateUnlocked && millis() - unlockStartTime > 10000) {
    lockGate();
  }
  
  delay(500);
}

void unlockGate(String bookingID, int duration) {
  Serial.println("Unlocking gate for booking: " + bookingID);
  digitalWrite(GATE_PIN, HIGH);
  gateUnlocked = true;
  unlockStartTime = millis();
  
  // Update Firebase to confirm the gate was unlocked
  FirebaseJson json;
  json.add("status", "unlocked");
  json.add("bookingID", bookingID);
  json.add("timestamp", String(millis()));
  
  if (Firebase.setJSON(fbdo, "/gateStatus", json)) {
    Serial.println("Gate status updated in Firebase");
  } else {
    Serial.println("Failed to update gate status");
    Serial.println("Reason: " + fbdo.errorReason());
  }
}

void lockGate() {
  Serial.println("Locking gate");
  digitalWrite(GATE_PIN, LOW);
  gateUnlocked = false;
  
  // Update Firebase to confirm the gate was locked
  FirebaseJson json;
  json.add("status", "locked");
  json.add("timestamp", String(millis()));
  
  if (Firebase.setJSON(fbdo, "/gateStatus", json)) {
    Serial.println("Gate status updated in Firebase");
  } else {
    Serial.println("Failed to update gate status");
    Serial.println("Reason: " + fbdo.errorReason());
  }
}

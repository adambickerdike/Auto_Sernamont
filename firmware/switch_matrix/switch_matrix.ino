/*
  100-Channel Signal Switching Matrix Controller (Exclusive Switching)
  Target: Arduino Nano
  Hardware: 7x TLC59282 cascaded shift registers, AQV258AX SSRs
*/

// --- Pin Definitions ---
const int PIN_BLANK = 9;   // D9  - Disables outputs when HIGH
const int PIN_LAT   = 10;  // D10 - Latch pin
const int PIN_DATA  = 11;  // D11 - Serial Data In
const int PIN_CLK   = 13;  // D13 - Serial Clock

const int NUM_SWITCHES = 100;
const int NUM_GROUPS = 10;
const int NUM_SHIFT_BITS = 112;

// Logical stage pin/pixel -> physical electrical switch for the current wiring.
const byte PIXEL_TO_ELECTRICAL_SWITCH[NUM_SWITCHES] = {
  26, 24, 21, 18, 14, 13, 9, 6, 3, 1,
  28, 27, 23, 19, 15, 12, 8, 4, 100, 99,
  31, 30, 25, 20, 16, 11, 7, 2, 97, 96,
  35, 33, 32, 29, 17, 10, 98, 95, 94, 92,
  38, 37, 36, 34, 22, 5, 93, 91, 90, 89,
  39, 40, 41, 43, 55, 72, 84, 86, 87, 88,
  42, 44, 45, 48, 60, 67, 79, 82, 83, 85,
  46, 47, 52, 57, 61, 66, 70, 75, 80, 81,
  49, 50, 54, 58, 62, 65, 69, 73, 77, 78,
  51, 53, 56, 59, 63, 64, 68, 71, 74, 76,
};

// Array to hold the desired physical electrical-switch state.
bool channelStates[NUM_SWITCHES] = {false};

void switchToLogicalPin(int pin);
void switchToChannel(int channel);
void switchToElectricalSwitch(int electricalSwitch, int logicalPin);
void turnAllOff();
void updateHardware();

void setup() {
  Serial.begin(9600);

  pinMode(PIN_BLANK, OUTPUT);
  pinMode(PIN_LAT, OUTPUT);
  pinMode(PIN_DATA, OUTPUT);
  pinMode(PIN_CLK, OUTPUT);

  digitalWrite(PIN_BLANK, HIGH);
  digitalWrite(PIN_LAT, LOW);
  digitalWrite(PIN_CLK, LOW);
  digitalWrite(PIN_DATA, LOW);

  updateHardware();
  digitalWrite(PIN_BLANK, LOW);

  Serial.println("=====================================");
  Serial.println("Signal Matrix Ready (Exclusive Mode, Logical Pin Mapping active).");
  Serial.println("- Type a logical pin/pixel (1-100) to switch via the configured wiring map.");
  Serial.println("- Type E<number> for a raw electrical switch/channel command.");
  Serial.println("- Type 0 to turn ALL channels OFF.");
  Serial.println("=====================================");
}

void loop() {
  if (Serial.available() > 0) {
    String input = Serial.readStringUntil('\n');
    input.trim(); 
    
    if (input.length() > 0) {
      if (input.length() > 1 && (input.charAt(0) == 'E' || input.charAt(0) == 'e')) {
        int electricalSwitch = input.substring(1).toInt();

        if (electricalSwitch >= 1 && electricalSwitch <= NUM_SWITCHES) {
          switchToChannel(electricalSwitch);
        }
        else {
          Serial.println("Error: Raw electrical switch command must be E1..E100.");
        }
        return;
      }

      int pin = input.toInt();

      if (pin >= 1 && pin <= NUM_SWITCHES) {
        switchToLogicalPin(pin);
      }
      else if (pin == 0 && input == "0") {
        turnAllOff();
      }
      else {
        Serial.println("Error: Please enter 0, or a number between 1 and 100.");
      }
    }
  }
}

/**
 * Maps a logical stage pin/pixel to the physical electrical switch.
 */
void switchToLogicalPin(int pin) {
  int electricalSwitch = PIXEL_TO_ELECTRICAL_SWITCH[pin - 1];
  switchToElectricalSwitch(electricalSwitch, pin);
}

/**
 * Raw physical electrical-switch command used by Python automation.
 */
void switchToChannel(int channel) {
  switchToElectricalSwitch(channel, 0);
}

/**
 * Turns off all channels, then turns on ONLY the requested electrical switch.
 */
void switchToElectricalSwitch(int electricalSwitch, int logicalPin) {
  // Check if the requested switch is already the active one
  bool alreadyActive = channelStates[electricalSwitch - 1];

  // 1. Turn ALL channels OFF in the software memory
  for (int i = 0; i < NUM_SWITCHES; i++) {
    channelStates[i] = false;
  }

  // 2. If it wasn't already active, turn it ON.
  // (If it WAS already active, we leave everything off—acting as a toggle).
  if (!alreadyActive) {
    channelStates[electricalSwitch - 1] = true;
    if (logicalPin >= 1) {
      Serial.print("-> Logical Pin ");
      Serial.print(logicalPin);
      Serial.print(" -> Electrical Switch ");
      Serial.println(electricalSwitch);
    } else {
      Serial.print("-> Electrical Switch ");
      Serial.println(electricalSwitch);
    }
  } else {
    Serial.println("-> Matrix Disabled (All channels OFF).");
  }

  // 3. Push the new configuration to the hardware
  updateHardware();
}

/**
 * Safely disables the entire matrix.
 */
void turnAllOff() {
  for (int i = 0; i < NUM_SWITCHES; i++) {
    channelStates[i] = false;
  }
  Serial.println("-> ALL channels have been turned OFF.");
  updateHardware();
}

/**
 * Calculates group dependencies and shifts all 112 bits to the hardware.
 */
void updateHardware() {
  bool bitStream[NUM_SHIFT_BITS] = {false};

  // Map channels and enable the required Group relay
  for (int i = 0; i < NUM_SWITCHES; i++) {
    bitStream[i] = channelStates[i];

    if (channelStates[i] == true) {
      int groupIndex = i / 10;
      bitStream[100 + groupIndex] = true;
    }
  }

  // Shift out data
  digitalWrite(PIN_LAT, LOW);
  for (int i = NUM_SHIFT_BITS - 1; i >= 0; i--) {
    digitalWrite(PIN_CLK, LOW);
    digitalWrite(PIN_DATA, bitStream[i] ? HIGH : LOW);
    digitalWrite(PIN_CLK, HIGH);
  }

  // Latch data
  digitalWrite(PIN_CLK, LOW);
  digitalWrite(PIN_LAT, HIGH);
  digitalWrite(PIN_LAT, LOW);
}

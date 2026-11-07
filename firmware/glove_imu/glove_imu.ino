/*
 * MPU6050 -> AI Thinker ESP32-CAM -> raw sensor data over UDP on WiFi
 * -------------------------------------------------------------------
 * BOARD: "AI Thinker ESP32-CAM" (esp32 core 3.x).
 * SETUP: copy config.example.h to config.h in this folder and fill in your WiFi
 * details. Git ignores config.h, so your password stays on your computer.
 * The camera on the board is not used.
 *
 * WIRING:
 *   MPU6050 VCC -> ESP32-CAM 3V3
 *   MPU6050 GND -> ESP32-CAM GND
 *   MPU6050 SDA -> ESP32-CAM pin set in MPU_SDA_PIN (default GPIO13)
 *   MPU6050 SCL -> ESP32-CAM pin set in MPU_SCL_PIN (default GPIO14)
 *   Training button -> between GPIO12 (BUTTON_PIN) and GND. NEVER wire it to 3V3: GPIO12
 *   held high at power on stops the board booting. Wired to GND it is always safe.
 *   Power the ESP32-CAM from a solid 5 V supply, because WiFi draws current spikes
 *   that can reset the board on a weak one.
 *
 * CHOOSING PINS:
 *   GPIO13 and GPIO14 are the best choice, and are only shared with the SD card slot.
 *   Avoid GPIO12, GPIO15, GPIO2 and GPIO0, which affect booting if held high or low at
 *   power on, and GPIO16, which is used by PSRAM. GPIO1 and GPIO3 are the USB serial
 *   pins, and GPIO4 drives the flash LED.
 *
 * UDP STREAM:
 *   Sent to port UDP_PORT, at LAPTOP_IP if set, otherwise broadcast to the whole network.
 *   Each packet holds FRAMES_PER_PACKET frames, one after another, in this 19-byte format:
 *   0xA5 0x5A | timestamp in microseconds (uint32, little-endian)
 *   | ax ay az gx gy gz (int16, big-endian) | XOR of timestamp and sensor bytes
 *   The second byte is 0x5B instead of 0x5A while the training button is held, so every
 *   frame carries the button state and a lost packet cannot lose a press or release.
 *
 * SENSOR SETTINGS:
 *   Accel +-16 g (2048 LSB/g), gyro +-2000 deg/s (16.4 LSB per deg/s), 500 Hz.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "config.h"  // WIFI_SSID, WIFI_PASSWORD and LAPTOP_IP

// ---------------- USER CONFIGURATION ----------------

int MPU_SDA_PIN = 13;
int MPU_SCL_PIN = 14;

const uint16_t UDP_PORT = 5005;
// ------------------------------------------------------

const int STATUS_LED_PIN = 33;  // small red LED on the back of the board, on when driven LOW
const int BUTTON_PIN = 12;      // training button to GND; reads LOW while pressed
const uint8_t BUTTON_DEBOUNCE_SAMPLES = 5;  // 5 samples at 500 Hz = 10 ms of steady reading

const uint8_t MPU6050_ADDR = 0x68;  // change to 0x69 if AD0 pin is pulled high
const uint32_t I2C_CLOCK_HZ = 400000;
const uint16_t I2C_TIMEOUT_MS = 5;

const int FRAMES_PER_PACKET = 10;  // 10 frames at 500 Hz = 50 packets/s and 20 ms of delay
const int QUEUE_FRAMES = 100;
const uint32_t STATUS_PERIOD_MS = 5000;
const uint32_t CONFIG_CHECK_MS = 250;
const TickType_t SAMPLE_PERIOD_TICKS = pdMS_TO_TICKS(2);  // 500 Hz

const int FRAME_SIZE = 19;

const uint8_t REG_SMPLRT_DIV = 0x19;
const uint8_t REG_CONFIG = 0x1A;
const uint8_t REG_GYRO_CONFIG = 0x1B;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;
const uint8_t REG_PWR_MGMT_1 = 0x6B;

const uint8_t PWR_MGMT_1_VALUE = 0x01;    // wake up, use gyro clock
const uint8_t ACCEL_CONFIG_VALUE = 0x18;  // +-16 g

QueueHandle_t frameQueue;
volatile uint32_t samplesRead = 0;
volatile uint32_t samplesDropped = 0;
volatile uint32_t sensorErrors = 0;
volatile uint32_t packetsSent = 0;

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

int readRegister(uint8_t reg) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MPU6050_ADDR, (uint8_t)1) != 1) {
    return -1;
  }
  return Wire.read();
}

bool configureSensor() {
  return writeRegister(REG_PWR_MGMT_1, PWR_MGMT_1_VALUE)
      && writeRegister(REG_CONFIG, 0x01)         // 184 Hz low-pass, 1 kHz base rate
      && writeRegister(REG_SMPLRT_DIV, 0x01)     // 1 kHz / (1 + 1) = 500 Hz
      && writeRegister(REG_GYRO_CONFIG, 0x18)    // +-2000 deg/s
      && writeRegister(REG_ACCEL_CONFIG, ACCEL_CONFIG_VALUE);
}

// A power blip from an impact resets the sensor to asleep at +-2 g, while the ESP32
// keeps running. Its readings then look wrong forever, so check and set it up again.
void restoreSensorIfReset() {
  static uint32_t lastCheckMs = 0;
  if (millis() - lastCheckMs < CONFIG_CHECK_MS) {
    return;
  }
  lastCheckMs = millis();
  if (readRegister(REG_PWR_MGMT_1) != PWR_MGMT_1_VALUE
      || readRegister(REG_ACCEL_CONFIG) != ACCEL_CONFIG_VALUE) {
    configureSensor();
  }
}

// Returns the debounced button state. Called once per sample, so it needs no timer.
bool buttonPressed() {
  static bool stable = false;
  static uint8_t differentFor = 0;
  bool raw = digitalRead(BUTTON_PIN) == LOW;
  if (raw == stable) {
    differentFor = 0;
  } else if (++differentFor >= BUTTON_DEBOUNCE_SAMPLES) {
    stable = raw;
    differentFor = 0;
  }
  return stable;
}

bool readFrame(uint8_t* frame, bool pressed) {
  uint32_t timestamp = micros();

  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MPU6050_ADDR, (uint8_t)14) != 14) {
    return false;
  }

  // Layout: accel[0..5], temperature[6..7], gyro[8..13]
  uint8_t raw[14];
  for (uint8_t i = 0; i < 14; i++) {
    raw[i] = Wire.read();
  }

  frame[0] = 0xA5;
  frame[1] = pressed ? 0x5B : 0x5A;
  frame[2] = timestamp & 0xFF;
  frame[3] = (timestamp >> 8) & 0xFF;
  frame[4] = (timestamp >> 16) & 0xFF;
  frame[5] = (timestamp >> 24) & 0xFF;
  memcpy(&frame[6], &raw[0], 6);
  memcpy(&frame[12], &raw[8], 6);

  uint8_t checksum = 0;
  for (uint8_t i = 2; i < 18; i++) {
    checksum ^= frame[i];
  }
  frame[18] = checksum;
  return true;
}

// Runs on core 1, away from the WiFi stack on core 0, to keep sampling regular
void sensorTask(void*) {
  TickType_t lastWake = xTaskGetTickCount();
  uint8_t frame[FRAME_SIZE];
  while (true) {
    vTaskDelayUntil(&lastWake, SAMPLE_PERIOD_TICKS);
    restoreSensorIfReset();
    if (!readFrame(frame, buttonPressed())) {
      sensorErrors = sensorErrors + 1;
      continue;
    }
    samplesRead = samplesRead + 1;
    if (xQueueSend(frameQueue, frame, 0) != pdTRUE) {
      samplesDropped = samplesDropped + 1;
    }
  }
}

void networkTask(void*) {
  WiFiUDP udp;
  IPAddress laptop;
  bool useLaptopIp = laptop.fromString(LAPTOP_IP);

  uint8_t packet[FRAMES_PER_PACKET * FRAME_SIZE];
  int framesInPacket = 0;
  while (true) {
    xQueueReceive(frameQueue, &packet[framesInPacket * FRAME_SIZE], portMAX_DELAY);
    framesInPacket++;
    if (framesInPacket < FRAMES_PER_PACKET) {
      continue;
    }
    framesInPacket = 0;
    if (WiFi.status() != WL_CONNECTED) {
      continue;
    }
    if (udp.beginPacket(useLaptopIp ? laptop : WiFi.broadcastIP(), UDP_PORT)) {
      udp.write(packet, sizeof(packet));
      if (udp.endPacket()) {
        packetsSent = packetsSent + 1;
      }
    }
  }
}

void setup() {
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, LOW);
  pinMode(BUTTON_PIN, INPUT_PULLUP);

  Serial.begin(115200);
  Serial.println();

  if (strlen(WIFI_SSID) == 0) {
    Serial.println("ERROR: WIFI_SSID is empty. Fill in the WiFi details in config.h.");
    while (true) {
      delay(1000);
    }
  }

  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  if (!configureSensor()) {
    Serial.printf("ERROR: MPU6050 not found on SDA GPIO%d, SCL GPIO%d. Still retrying.\n", MPU_SDA_PIN, MPU_SCL_PIN);
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // WiFi power saving adds around 100 ms of delay
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.printf("Connecting to %s", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\nConnected. IP address: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("Sending UDP to %s port %u\n",
                strlen(LAPTOP_IP) > 0 ? LAPTOP_IP : WiFi.broadcastIP().toString().c_str(), UDP_PORT);

  frameQueue = xQueueCreate(QUEUE_FRAMES, FRAME_SIZE);
  xTaskCreatePinnedToCore(networkTask, "network", 4096, nullptr, 2, nullptr, 0);
  xTaskCreatePinnedToCore(sensorTask, "sensor", 4096, nullptr, 5, nullptr, 1);
}

void loop() {
  static uint32_t lastSamples = 0;
  static uint32_t lastErrors = 0;
  delay(STATUS_PERIOD_MS);
  uint32_t samples = samplesRead;
  uint32_t errors = sensorErrors;
  Serial.printf("Sensor %lu Hz, %lu read errors | %lu packets sent, %lu samples dropped | RSSI %d dBm\n",
                (unsigned long)((samples - lastSamples) * 1000 / STATUS_PERIOD_MS),
                (unsigned long)(errors - lastErrors),
                (unsigned long)packetsSent, (unsigned long)samplesDropped, WiFi.RSSI());
  lastSamples = samples;
  lastErrors = errors;
}

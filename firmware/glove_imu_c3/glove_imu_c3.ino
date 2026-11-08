/*
 * MPU6050 / MPU-6500 -> ESP32-C3 -> raw sensor data over UDP on WiFi
 * ------------------------------------------------------------------
 * The ESP32-CAM glove's firmware (firmware/glove_imu), for an ESP32-C3: the same 19-byte frames, ranges and
 * 500 Hz, so glove_receiver.py, glove_check.py and the whole pipeline read it unchanged.
 *
 * BOARD: "ESP32C3 Dev Module", Tools > USB CDC On Boot = Enabled (FQBN esp32:esp32:esp32c3:CDCOnBoot=cdc),
 * so the status lines show on the C3's own USB port.
 * SETUP: copy config.example.h to config.h in this folder and fill in the WiFi details. Git ignores config.h.
 *
 * WIRING: MPU VCC -> 3V3, GND -> GND, SDA -> GPIO1, SCL -> GPIO3, AD0 -> GND or loose.
 *   Avoid GPIO2, GPIO8 and GPIO9 (boot pins) and GPIO18/19 (USB). No training button on this board.
 *
 * PORT: one sketch for both arms. Left (port 5005) by default. For the RIGHT arm (port 5006, --arm right) add
 *   --build-property "compiler.cpp.extra_flags=-DGLOVE_UDP_PORT=5006" to arduino-cli compile.
 *
 * UDP STREAM: FRAMES_PER_PACKET frames per packet, each:
 *   0xA5 0x5A | timestamp in microseconds (uint32, little-endian)
 *   | ax ay az gx gy gz (int16, big-endian) | XOR of timestamp and sensor bytes
 * SENSOR SETTINGS: accel +-16 g (2048 LSB/g), gyro +-2000 deg/s (16.4 LSB per deg/s), 500 Hz.
 *
 * The C3 has one core, so sampling runs as a higher-priority task than sending. Small C3 boards with a chip
 * antenna often fail to join WiFi at full transmit power, so it is turned down (WIFI_TX_POWER).
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "config.h"  // WIFI_SSID, WIFI_PASSWORD and LAPTOP_IP

// ---------------- USER CONFIGURATION ----------------
int MPU_SDA_PIN = 1;
int MPU_SCL_PIN = 3;
#ifndef GLOVE_UDP_PORT
#define GLOVE_UDP_PORT 5005  // left arm. Flash the right-arm board with -DGLOVE_UDP_PORT=5006 (see PORT above)
#endif
const uint16_t UDP_PORT = GLOVE_UDP_PORT;
const wifi_power_t WIFI_TX_POWER = WIFI_POWER_8_5dBm;  // raise if the signal is weak and it does join
// ------------------------------------------------------

uint8_t mpuAddr = 0x68;  // 0x69 if AD0 is pulled high; found by scanning at start-up
const uint32_t I2C_CLOCK_HZ = 400000;
const uint16_t I2C_TIMEOUT_MS = 5;

const int FRAME_SIZE = 19;
const int FRAMES_PER_PACKET = 10;  // 10 frames at 500 Hz = 50 packets/s and 20 ms of delay
const int QUEUE_FRAMES = 100;
const uint32_t STATUS_PERIOD_MS = 5000;
const uint32_t CONFIG_CHECK_MS = 250;
const TickType_t SAMPLE_PERIOD_TICKS = pdMS_TO_TICKS(2);  // 500 Hz

const uint8_t REG_SMPLRT_DIV = 0x19, REG_CONFIG = 0x1A, REG_GYRO_CONFIG = 0x1B, REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B, REG_PWR_MGMT_1 = 0x6B, REG_WHO_AM_I = 0x75;
const uint8_t PWR_MGMT_1_VALUE = 0x01;    // wake up, use gyro clock
const uint8_t ACCEL_CONFIG_VALUE = 0x18;  // +-16 g

QueueHandle_t frameQueue;
volatile uint32_t samplesRead = 0, samplesDropped = 0, sensorErrors = 0, packetsSent = 0, resets = 0;

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

int readRegister(uint8_t reg) {
  Wire.beginTransmission(mpuAddr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(mpuAddr, (uint8_t)1) != 1) {
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

// A power blip resets the sensor to asleep at +-2 g while the C3 keeps running, so check and set it up again.
void restoreSensorIfReset() {
  static uint32_t lastCheckMs = 0;
  if (millis() - lastCheckMs < CONFIG_CHECK_MS) {
    return;
  }
  lastCheckMs = millis();
  if (readRegister(REG_PWR_MGMT_1) != PWR_MGMT_1_VALUE || readRegister(REG_ACCEL_CONFIG) != ACCEL_CONFIG_VALUE) {
    resets = resets + 1;
    configureSensor();
  }
}

bool readFrame(uint8_t* frame) {
  uint32_t timestamp = micros();
  Wire.beginTransmission(mpuAddr);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(mpuAddr, (uint8_t)14) != 14) {
    return false;
  }
  uint8_t raw[14];  // accel[0..5], temperature[6..7], gyro[8..13]
  for (uint8_t i = 0; i < 14; i++) {
    raw[i] = Wire.read();
  }
  frame[0] = 0xA5;
  frame[1] = 0x5A;  // no training button on this board
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

void sensorTask(void*) {
  TickType_t lastWake = xTaskGetTickCount();
  uint8_t frame[FRAME_SIZE];
  while (true) {
    vTaskDelayUntil(&lastWake, SAMPLE_PERIOD_TICKS);
    restoreSensorIfReset();
    if (!readFrame(frame)) {
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
    if (++framesInPacket < FRAMES_PER_PACKET) {
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

void findSensor() {
  for (uint8_t a : {0x68, 0x69}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      mpuAddr = a;
      int who = readRegister(REG_WHO_AM_I);
      Serial.printf("MPU found at 0x%02X, WHO_AM_I 0x%02X (%s)\n", a, who & 0xFF,
                    who == 0x68 ? "MPU6050" : who == 0x70 ? "MPU-6500, no magnetometer"
                    : who == 0x71 ? "MPU-9250" : who == 0x73 ? "MPU-9255" : "unknown");
      return;
    }
  }
  Serial.printf("ERROR: no MPU answers on SDA GPIO%d, SCL GPIO%d. Still retrying.\n", MPU_SDA_PIN, MPU_SCL_PIN);
}

void setup() {
  Serial.begin(115200);
  Serial.setTxTimeoutMs(0);  // never stall when no laptop is reading the USB port
  delay(1500);               // time for the USB port to reappear after a reset, so the start-up lines show
  Serial.printf("\nESP32-C3 glove, %s arm: UDP port %u, 19-byte frames, +-16 g, +-2000 deg/s, 500 Hz\n",
                UDP_PORT == 5006 ? "RIGHT" : "LEFT", UDP_PORT);

  if (strlen(WIFI_SSID) == 0) {
    Serial.println("ERROR: WIFI_SSID is empty. Fill in the WiFi details in config.h.");
    while (true) {
      delay(1000);
    }
  }

  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  findSensor();
  configureSensor();

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // WiFi power saving adds around 100 ms of delay
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  WiFi.setTxPower(WIFI_TX_POWER);
  Serial.printf("Connecting to %s", WIFI_SSID);
  while (WiFi.status() != WL_CONNECTED) {
    delay(250);
    Serial.print(".");
  }
  Serial.printf("\nConnected. IP address: %s\n", WiFi.localIP().toString().c_str());
  Serial.printf("Sending UDP to %s port %u\n",
                strlen(LAPTOP_IP) > 0 ? LAPTOP_IP : WiFi.broadcastIP().toString().c_str(), UDP_PORT);

  frameQueue = xQueueCreate(QUEUE_FRAMES, FRAME_SIZE);
  xTaskCreate(networkTask, "network", 4096, nullptr, 2, nullptr);
  xTaskCreate(sensorTask, "sensor", 4096, nullptr, 5, nullptr);
}

void loop() {
  static uint32_t lastSamples = 0, lastErrors = 0;
  delay(STATUS_PERIOD_MS);
  uint32_t samples = samplesRead, errors = sensorErrors;
  Serial.printf("Sensor %lu Hz, %lu read errors, %lu resets | %lu packets sent, %lu samples dropped | RSSI %d dBm\n",
                (unsigned long)((samples - lastSamples) * 1000 / STATUS_PERIOD_MS), (unsigned long)(errors - lastErrors),
                (unsigned long)resets, (unsigned long)packetsSent, (unsigned long)samplesDropped, WiFi.RSSI());
  lastSamples = samples;
  lastErrors = errors;
}

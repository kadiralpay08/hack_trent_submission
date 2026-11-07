/*
 * LEFT GLOVE: MPU-9250 / MPU-9255 / MPU-6500 -> ESP32-S3-WROOM-1 -> raw sensor data over UDP on WiFi
 * ------------------------------------------------------------------------------------------------
 * The same job as firmware/glove_imu (the old left glove, ESP32-CAM + MPU6050), plus the magnetometer.
 * Which arm it is depends only on UDP_PORT: 5005 = left (now), 5006 = right.
 *
 * BOARD: "ESP32S3 Dev Module" (esp32 core 3.x), FQBN esp32:esp32:esp32s3.
 * SETUP: copy config.example.h to config.h in this folder and fill in your WiFi details (the same
 * hotspot and laptop address as the left glove). Git ignores config.h.
 *
 * WIRING:
 *   MPU VCC -> ESP32-S3 3V3        MPU GND -> GND
 *   MPU SDA -> GPIO2 (MPU_SDA_PIN) MPU SCL -> GPIO1 (MPU_SCL_PIN)
 *   MPU AD0 -> GND (address 0x68; tie it high for 0x69 and change MPU_ADDR)
 *   Training button -> between GPIO5 (BUTTON_PIN) and GND. GPIO5 is not a boot pin, so this is safe.
 *   Avoid GPIO0, 3, 45 and 46 (boot pins), 19 and 20 (USB) and 26 to 37 (flash and PSRAM).
 *
 * WHICH SENSOR: the MPU-9250 and MPU-9255 contain an AK8963 magnetometer. The MPU-6500 does NOT. This
 * sketch reads WHO_AM_I, prints what it found, and with a 6500 sends the magnetometer as invalid
 * (status bit 0 clear) while everything else works the same.
 *
 * UDP STREAM: port UDP_PORT (5005 = left arm; the laptop tells the gloves apart by port, right = 5006),
 * to LAPTOP_IP if set, otherwise broadcast. Each packet holds FRAMES_PER_PACKET frames of 26 bytes:
 *   0xA5 | 0x5C (button up) or 0x5D (button held)
 *   | timestamp in microseconds (uint32, little-endian)
 *   | ax ay az gx gy gz (int16, big-endian, in the MPU's own axes, exactly as the left glove)
 *   | mx my mz (int16, big-endian, AK8963's own axes, already corrected with its factory adjustment;
 *     0.15 uT per count). The receiver turns them into the accelerometer's axes: x<-my, y<-mx, z<- -mz.
 *   | status: bit 0 = magnetometer working, bit 1 = this frame carries a new magnetometer reading
 *   | XOR of bytes 2 to 24
 * The magnetometer updates at 100 Hz, the rest at 500 Hz, so frames in between repeat its last value.
 *
 * SENSOR SETTINGS: accel +-16 g (2048 LSB/g), gyro +-2000 deg/s (16.4 LSB per deg/s), 500 Hz;
 * magnetometer 16-bit, continuous 100 Hz.
 */

#include <WiFi.h>
#include <WiFiUdp.h>
#include <Wire.h>

#include "config.h"  // WIFI_SSID, WIFI_PASSWORD and LAPTOP_IP

// ---------------- USER CONFIGURATION ----------------
int MPU_SDA_PIN = 2;
int MPU_SCL_PIN = 1;
const int BUTTON_PIN = 5;
const uint16_t UDP_PORT = 5005;  // 5005 = left arm, 5006 = right arm
// ------------------------------------------------------

const uint8_t MPU_ADDR = 0x68;
const uint8_t MAG_ADDR = 0x0C;  // AK8963, reachable once the MPU's I2C bypass is on
const uint32_t I2C_CLOCK_HZ = 400000;
const uint16_t I2C_TIMEOUT_MS = 5;

const int FRAMES_PER_PACKET = 10;
const int QUEUE_FRAMES = 100;
const uint32_t STATUS_PERIOD_MS = 5000;
const uint32_t CONFIG_CHECK_MS = 250;
const TickType_t SAMPLE_PERIOD_TICKS = pdMS_TO_TICKS(2);  // 500 Hz
const int MAG_EVERY = 5;                                   // check the magnetometer every 5 samples (10 ms)
const uint8_t BUTTON_DEBOUNCE_SAMPLES = 5;                 // 10 ms of steady reading

const int FRAME_SIZE = 26;

// MPU registers (MPU6050-compatible for accel and gyro)
const uint8_t REG_SMPLRT_DIV = 0x19;
const uint8_t REG_CONFIG = 0x1A;
const uint8_t REG_GYRO_CONFIG = 0x1B;
const uint8_t REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_INT_PIN_CFG = 0x37;
const uint8_t REG_ACCEL_XOUT_H = 0x3B;
const uint8_t REG_USER_CTRL = 0x6A;
const uint8_t REG_PWR_MGMT_1 = 0x6B;
const uint8_t REG_WHO_AM_I = 0x75;
const uint8_t PWR_MGMT_1_VALUE = 0x01;    // wake up, best clock
const uint8_t ACCEL_CONFIG_VALUE = 0x18;  // +-16 g
const uint8_t INT_PIN_CFG_BYPASS = 0x02;  // BYPASS_EN: the AK8963 appears directly on our I2C bus

// AK8963 registers
const uint8_t AK_WIA = 0x00;    // reads 0x48
const uint8_t AK_ST1 = 0x02;    // bit 0: new data ready
const uint8_t AK_HXL = 0x03;    // HXL..HZH, then ST2
const uint8_t AK_CNTL1 = 0x0A;
const uint8_t AK_ASAX = 0x10;   // factory sensitivity adjustment, 3 bytes
const uint8_t AK_MODE_OFF = 0x00, AK_MODE_FUSE = 0x0F, AK_MODE_100HZ_16BIT = 0x16;

QueueHandle_t frameQueue;
volatile uint32_t samplesRead = 0, samplesDropped = 0, sensorErrors = 0, packetsSent = 0, magReadings = 0;
bool magPresent = false;
float magAdjust[3] = {1, 1, 1};
int16_t lastMag[3] = {0, 0, 0};
bool magFresh = false;
uint8_t whoAmI = 0;

bool writeRegister(uint8_t addr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegisters(uint8_t addr, uint8_t reg, uint8_t* out, uint8_t n) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(addr, n) != n) {
    return false;
  }
  for (uint8_t i = 0; i < n; i++) {
    out[i] = Wire.read();
  }
  return true;
}

int readRegister(uint8_t addr, uint8_t reg) {
  uint8_t v;
  return readRegisters(addr, reg, &v, 1) ? v : -1;
}

bool configureSensor() {
  return writeRegister(MPU_ADDR, REG_PWR_MGMT_1, PWR_MGMT_1_VALUE)
      && writeRegister(MPU_ADDR, REG_CONFIG, 0x01)        // 184 Hz low-pass, 1 kHz base rate
      && writeRegister(MPU_ADDR, REG_SMPLRT_DIV, 0x01)    // 1 kHz / (1 + 1) = 500 Hz
      && writeRegister(MPU_ADDR, REG_GYRO_CONFIG, 0x18)   // +-2000 deg/s
      && writeRegister(MPU_ADDR, REG_ACCEL_CONFIG, ACCEL_CONFIG_VALUE)
      && writeRegister(MPU_ADDR, REG_USER_CTRL, 0x00)     // MPU's own I2C master off...
      && writeRegister(MPU_ADDR, REG_INT_PIN_CFG, INT_PIN_CFG_BYPASS);  // ...so the AK8963 is on our bus
}

// Finds the AK8963, reads its factory adjustment, and starts it at 100 Hz. False on an MPU-6500.
bool configureMag() {
  if (readRegister(MAG_ADDR, AK_WIA) != 0x48) {
    return false;
  }
  uint8_t asa[3];
  writeRegister(MAG_ADDR, AK_CNTL1, AK_MODE_OFF);
  delay(10);
  writeRegister(MAG_ADDR, AK_CNTL1, AK_MODE_FUSE);
  delay(10);
  if (!readRegisters(MAG_ADDR, AK_ASAX, asa, 3)) {
    return false;
  }
  for (int i = 0; i < 3; i++) {
    magAdjust[i] = (asa[i] - 128) / 256.0f + 1.0f;  // AK8963 datasheet: H * ((ASA - 128) * 0.5 / 128 + 1)
  }
  writeRegister(MAG_ADDR, AK_CNTL1, AK_MODE_OFF);
  delay(10);
  return writeRegister(MAG_ADDR, AK_CNTL1, AK_MODE_100HZ_16BIT);
}

// A power blip from an impact can reset the MPU (asleep, +-2 g, bypass off) while the ESP32 keeps
// running. Check now and then and set everything up again, as the left glove does.
void restoreSensorIfReset() {
  static uint32_t lastCheckMs = 0;
  if (millis() - lastCheckMs < CONFIG_CHECK_MS) {
    return;
  }
  lastCheckMs = millis();
  if (readRegister(MPU_ADDR, REG_PWR_MGMT_1) != PWR_MGMT_1_VALUE
      || readRegister(MPU_ADDR, REG_ACCEL_CONFIG) != ACCEL_CONFIG_VALUE
      || readRegister(MPU_ADDR, REG_INT_PIN_CFG) != INT_PIN_CFG_BYPASS) {
    if (configureSensor() && magPresent) {
      configureMag();
    }
  }
}

// Reads a new magnetometer value if one is ready. Keeps the last good value otherwise.
void updateMag() {
  if (!magPresent) {
    return;
  }
  int st1 = readRegister(MAG_ADDR, AK_ST1);
  if (st1 < 0 || !(st1 & 0x01)) {
    return;  // nothing new yet
  }
  uint8_t raw[7];  // HXL HXH HYL HYH HZL HZH ST2 (reading ST2 releases the next measurement)
  if (!readRegisters(MAG_ADDR, AK_HXL, raw, 7) || (raw[6] & 0x08)) {
    return;  // read failed, or magnetic overflow
  }
  for (int i = 0; i < 3; i++) {
    int16_t v = (int16_t)(raw[2 * i] | (raw[2 * i + 1] << 8));  // the AK8963 is little-endian
    lastMag[i] = (int16_t)constrain(v * magAdjust[i], -32768.0f, 32767.0f);
  }
  magFresh = true;
  magReadings = magReadings + 1;
}

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
  uint8_t raw[14];  // accel[0..5], temperature[6..7], gyro[8..13]
  if (!readRegisters(MPU_ADDR, REG_ACCEL_XOUT_H, raw, 14)) {
    return false;
  }
  frame[0] = 0xA5;
  frame[1] = pressed ? 0x5D : 0x5C;
  frame[2] = timestamp & 0xFF;
  frame[3] = (timestamp >> 8) & 0xFF;
  frame[4] = (timestamp >> 16) & 0xFF;
  frame[5] = (timestamp >> 24) & 0xFF;
  memcpy(&frame[6], &raw[0], 6);
  memcpy(&frame[12], &raw[8], 6);
  for (int i = 0; i < 3; i++) {  // big-endian, like the accelerometer and gyro
    frame[18 + 2 * i] = (lastMag[i] >> 8) & 0xFF;
    frame[19 + 2 * i] = lastMag[i] & 0xFF;
  }
  frame[24] = (magPresent ? 0x01 : 0x00) | (magFresh ? 0x02 : 0x00);
  magFresh = false;
  uint8_t checksum = 0;
  for (uint8_t i = 2; i < 25; i++) {
    checksum ^= frame[i];
  }
  frame[25] = checksum;
  return true;
}

// Runs on core 1, away from the WiFi stack on core 0, to keep sampling regular
void sensorTask(void*) {
  TickType_t lastWake = xTaskGetTickCount();
  uint8_t frame[FRAME_SIZE];
  int sinceMag = 0;
  while (true) {
    vTaskDelayUntil(&lastWake, SAMPLE_PERIOD_TICKS);
    restoreSensorIfReset();
    if (++sinceMag >= MAG_EVERY) {
      sinceMag = 0;
      updateMag();
    }
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

void setup() {
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("Glove (ESP32-S3 + MPU-9250/6500)");

  if (strlen(WIFI_SSID) == 0) {
    Serial.println("ERROR: WIFI_SSID is empty. Fill in the WiFi details in config.h.");
    while (true) {
      delay(1000);
    }
  }

  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  whoAmI = (uint8_t)readRegister(MPU_ADDR, REG_WHO_AM_I);
  const char* model = whoAmI == 0x71 ? "MPU-9250" : whoAmI == 0x73 ? "MPU-9255" : whoAmI == 0x70 ? "MPU-6500"
                    : whoAmI == 0x68 ? "MPU-6050 or MPU-9150" : "unknown";
  Serial.printf("WHO_AM_I = 0x%02X (%s)\n", whoAmI, model);
  if (!configureSensor()) {
    Serial.printf("ERROR: motion sensor not found on SDA GPIO%d, SCL GPIO%d. Still retrying.\n", MPU_SDA_PIN, MPU_SCL_PIN);
  }
  magPresent = configureMag();
  Serial.println(magPresent ? "Magnetometer (AK8963) found, 100 Hz."
                            : "No magnetometer found (normal for an MPU-6500). Sending it as invalid.");

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
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
  static uint32_t lastSamples = 0, lastErrors = 0, lastMagCount = 0;
  delay(STATUS_PERIOD_MS);
  uint32_t samples = samplesRead, errors = sensorErrors, mags = magReadings;
  Serial.printf("Sensor %lu Hz, %lu read errors | magnetometer %lu Hz | %lu packets sent, %lu dropped | RSSI %d dBm\n",
                (unsigned long)((samples - lastSamples) * 1000 / STATUS_PERIOD_MS), (unsigned long)(errors - lastErrors),
                (unsigned long)((mags - lastMagCount) * 1000 / STATUS_PERIOD_MS), (unsigned long)packetsSent,
                (unsigned long)samplesDropped, WiFi.RSSI());
  lastSamples = samples;
  lastErrors = errors;
  lastMagCount = mags;
}

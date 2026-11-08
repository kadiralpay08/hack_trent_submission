/*
 * MPU6050 / MPU-6500 -> ESP32-C3 -> raw sensor data over USB serial
 * -----------------------------------------------------------------
 * The same data as the WiFi glove (firmware/glove_imu), sent down the C3's own USB port instead, so a
 * sensor can be tested or used with no WiFi at all. Check it with: py usb_glove_check.py --port COM9
 *
 * BOARD: "ESP32C3 Dev Module", with Tools > USB CDC On Boot = Enabled
 * (FQBN esp32:esp32:esp32c3:CDCOnBoot=cdc). Without that, nothing arrives on the USB port.
 *
 * WIRING:
 *   MPU VCC -> 3V3, GND -> GND, SDA -> GPIO1 (MPU_SDA_PIN), SCL -> GPIO3 (MPU_SCL_PIN), AD0 -> GND or loose.
 *   Avoid GPIO2, GPIO8 and GPIO9 (they set how the C3 boots) and GPIO18/19 (the USB port).
 *   The C3 has only one I2C controller, so the board's OLED (on its own pins) is not used.
 *
 * STREAM (binary, the glove's 19-byte frame, back to back):
 *   0xA5 0x5A | timestamp in microseconds (uint32, little-endian)
 *   | ax ay az gx gy gz (int16, big-endian) | XOR of timestamp and sensor bytes
 *   Text lines ("# ESP32...", "# MPU...", "# ERROR...", "# status...", ending in a newline) are mixed in,
 *   between frames: at start-up and every 5 s. A frame reader skips them as junk; usb_glove_check.py prints them.
 *
 * SENSOR SETTINGS (as the glove): accel +-16 g (2048 LSB/g), gyro +-2000 deg/s (16.4 LSB per deg/s), 500 Hz.
 */

#include <Wire.h>

// ---------------- USER CONFIGURATION ----------------
int MPU_SDA_PIN = 1;
int MPU_SCL_PIN = 3;
// ------------------------------------------------------

uint8_t mpuAddr = 0x68;  // 0x69 if AD0 is pulled high; found by scanning at start-up
const uint32_t I2C_CLOCK_HZ = 400000;
const uint16_t I2C_TIMEOUT_MS = 5;

const int FRAME_SIZE = 19;
const int FRAMES_PER_WRITE = 10;  // 10 frames at 500 Hz = one USB write every 20 ms
const uint32_t SAMPLE_PERIOD_US = 2000;  // 500 Hz
const uint32_t STATUS_PERIOD_MS = 5000;
const uint32_t CONFIG_CHECK_MS = 250;

const uint8_t REG_SMPLRT_DIV = 0x19, REG_CONFIG = 0x1A, REG_GYRO_CONFIG = 0x1B, REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B, REG_PWR_MGMT_1 = 0x6B, REG_WHO_AM_I = 0x75;
const uint8_t PWR_MGMT_1_VALUE = 0x01;    // wake up, use gyro clock
const uint8_t ACCEL_CONFIG_VALUE = 0x18;  // +-16 g

uint32_t samplesRead = 0, sensorErrors = 0, resets = 0;

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
    resets++;
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

void findSensor() {
  for (uint8_t a : {0x68, 0x69}) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      mpuAddr = a;
      int who = readRegister(REG_WHO_AM_I);
      Serial.printf("# MPU found at 0x%02X, WHO_AM_I 0x%02X (%s)\n", a, who & 0xFF,
                    who == 0x68 ? "MPU6050" : who == 0x70 ? "MPU-6500, no magnetometer"
                    : who == 0x71 ? "MPU-9250" : who == 0x73 ? "MPU-9255" : "unknown");
      return;
    }
  }
  Serial.printf("# ERROR: no MPU answers on SDA GPIO%d, SCL GPIO%d. Check 3V3, GND, SDA, SCL. Still retrying.\n",
                MPU_SDA_PIN, MPU_SCL_PIN);
}

void setup() {
  Serial.begin(115200);  // native USB: the baud rate is ignored, it runs at full USB speed
  Serial.setTxTimeoutMs(0);  // never stall sampling when nothing on the laptop is reading
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 2000) {
    delay(10);
  }
  Serial.println("\n# ESP32-C3 USB glove: 19-byte frames, +-16 g, +-2000 deg/s, 500 Hz");
  Wire.begin(MPU_SDA_PIN, MPU_SCL_PIN, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  findSensor();
  if (!configureSensor()) {
    Serial.println("# ERROR: could not set up the MPU. Still retrying.");
  }
}

void loop() {
  static uint8_t buffer[FRAMES_PER_WRITE * FRAME_SIZE];
  static int frames = 0;
  static uint32_t nextUs = micros();
  static uint32_t lastStatusMs = 0, lastSamples = 0, lastErrors = 0;

  while ((int32_t)(micros() - nextUs) < 0) {
  }
  nextUs += SAMPLE_PERIOD_US;
  if ((int32_t)(micros() - nextUs) > 50000) {
    nextUs = micros() + SAMPLE_PERIOD_US;  // fell far behind (a long I2C timeout): don't try to catch up
  }

  restoreSensorIfReset();
  if (readFrame(&buffer[frames * FRAME_SIZE])) {
    samplesRead++;
    if (++frames == FRAMES_PER_WRITE) {
      Serial.write(buffer, sizeof(buffer));
      frames = 0;
    }
  } else {
    sensorErrors++;
  }

  if (millis() - lastStatusMs >= STATUS_PERIOD_MS && frames == 0) {
    uint32_t elapsed = millis() - lastStatusMs;
    if (lastStatusMs) {
      Serial.printf("# status: sensor %lu Hz, %lu read errors, %lu sensor resets\n",
                    (unsigned long)((samplesRead - lastSamples) * 1000 / elapsed),
                    (unsigned long)(sensorErrors - lastErrors), (unsigned long)resets);
    }
    lastStatusMs = millis();
    lastSamples = samplesRead;
    lastErrors = sensorErrors;
  }
}

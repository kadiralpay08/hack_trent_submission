/*
 * MPU6050 -> Arduino Uno -> High-speed binary stream for punch_tracker.py
 * -----------------------------------------------------------------------
 * LIBRARIES NEEDED:
 *   - Wire.h  (built into the Arduino IDE, no install needed)
 *
 * WIRING:
 *   MPU6050 VCC -> Arduino 5V (or 3.3V, check your board)
 *   MPU6050 GND -> Arduino GND
 *   MPU6050 SCL -> Arduino A5 (Uno hardware I2C clock pin)
 *   MPU6050 SDA -> Arduino A4 (Uno hardware I2C data pin)
 *
 * USAGE:
 *   Upload this sketch, close the Serial Monitor and Serial Plotter, then run
 *   punch_tracker.py. The output is binary, so it looks like garbage in the
 *   Serial Monitor. If the sensor is not found, an error message is printed
 *   instead, which is readable in the Serial Monitor at SERIAL_BAUD.
 *
 * FRAME FORMAT (19 bytes):
 *   0xA5 0x5A | timestamp in microseconds (uint32, little-endian)
 *   | ax ay az gx gy gz (int16, big-endian) | XOR of timestamp and sensor bytes
 *
 * SENSOR SETTINGS:
 *   Accel +-16 g (2048 LSB/g), gyro +-2000 deg/s (16.4 LSB per deg/s).
 *   If you change these, change the matching constants in punch_tracker.py.
 */

#include <Wire.h>

// ---------------- USER CONFIGURATION ----------------
#define MPU6050_ADDR 0x68      // change to 0x69 if AD0 pin is pulled high
#define SERIAL_BAUD 500000     // must match --baud in punch_tracker.py
#define SAMPLE_PERIOD_US 2000  // 2000 us = 500 Hz
// ------------------------------------------------------

#define REG_SMPLRT_DIV   0x19
#define REG_CONFIG       0x1A
#define REG_GYRO_CONFIG  0x1B
#define REG_ACCEL_CONFIG 0x1C
#define REG_ACCEL_XOUT_H 0x3B
#define REG_PWR_MGMT_1   0x6B

#define WIRE_TIMEOUT_US 3000
#define CONFIG_CHECK_US 250000UL
#define PWR_MGMT_1_VALUE 0x01    // wake up, use gyro clock
#define ACCEL_CONFIG_VALUE 0x18  // +-16 g

unsigned long nextSampleMicros;
unsigned long lastConfigCheckMicros;

bool writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

int readRegister(uint8_t reg) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(MPU6050_ADDR, 1, true) != 1) {
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

// A power blip from an impact resets the sensor to asleep at +-2 g, while the Uno keeps
// running. Its readings then look wrong forever, so check for that and set it up again.
void restoreSensorIfReset() {
  if (micros() - lastConfigCheckMicros < CONFIG_CHECK_US) {
    return;
  }
  lastConfigCheckMicros = micros();
  if (readRegister(REG_PWR_MGMT_1) != PWR_MGMT_1_VALUE
      || readRegister(REG_ACCEL_CONFIG) != ACCEL_CONFIG_VALUE) {
    configureSensor();
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();
  Wire.setClock(400000);
  // Impacts can jolt the wiring, and without a timeout a bad connection hangs I2C forever
  Wire.setWireTimeout(WIRE_TIMEOUT_US, true);

  while (!configureSensor()) {
    Serial.println("ERROR: MPU6050 not found. Check wiring and MPU6050_ADDR.");
    delay(1000);
  }

  delay(100);
  nextSampleMicros = micros();
  lastConfigCheckMicros = nextSampleMicros;
}

void loop() {
  if ((long)(micros() - nextSampleMicros) < 0) {
    return;
  }
  nextSampleMicros += SAMPLE_PERIOD_US;

  restoreSensorIfReset();

  uint32_t timestamp = micros();

  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0) {
    return;
  }
  if (Wire.requestFrom(MPU6050_ADDR, 14, true) != 14) {
    return;
  }

  // Layout: accel[0..5], temperature[6..7], gyro[8..13]
  uint8_t raw[14];
  for (uint8_t i = 0; i < 14; i++) {
    raw[i] = Wire.read();
  }

  uint8_t frame[19];
  frame[0] = 0xA5;
  frame[1] = 0x5A;
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

  Serial.write(frame, sizeof(frame));
}

/*
 * MPU6050 HEALTH CHECK for the ESP32-CAM. USB serial only: no WiFi, nothing to configure.
 * ------------------------------------------------------------------------------------
 * Is the sensor still alive? Flash this, open Serial Monitor at 115200 baud, and read the verdict.
 * Flash the real glove firmware (firmware/glove_imu) again afterwards.
 *
 * BOARD: "AI Thinker ESP32-CAM" (esp32 core 3.x), FQBN esp32:esp32:esp32cam.
 * WIRING: the same as the glove, so nothing needs rewiring:
 *   MPU6050 VCC -> 3V3, GND -> GND, SDA -> GPIO13 (MPU_SDA_PIN), SCL -> GPIO14 (MPU_SCL_PIN), AD0 -> GND
 *
 * WHAT IT CHECKS, every second (200 readings):
 *   1. Something answers on the I2C bus at all, and at which address (it also tries SDA and SCL swapped,
 *      to catch crossed wires).
 *   2. WHO_AM_I says what chip it is (0x68 = MPU6050; other values are clones or a different chip).
 *   3. Reads succeed, with no I2C errors.
 *   4. The readings move. A dead sensor often returns the same numbers forever, all zeros, or all -1.
 *   5. At rest, gravity reads close to 1 g, and the gyro reads close to 0 deg/s. Keep it STILL for this.
 *   6. The temperature is believable.
 *
 * VERDICT LINE: "ALIVE" when everything passes. Otherwise it says what failed.
 *
 * RED LED on the back of the board (GPIO33), so you can also read it on the breadboard, away from USB:
 *   one short blink a second   = ALIVE
 *   slow blink (half a second) = found, but its data is bad (frozen, wrong gravity, I2C errors)
 *   fast flicker               = nothing answers on the I2C bus (dead, unpowered or miswired)
 */

#include <Wire.h>

// ---------------- USER CONFIGURATION ----------------
int MPU_SDA_PIN = 13;
int MPU_SCL_PIN = 14;
// ------------------------------------------------------

const int STATUS_LED_PIN = 33;  // on when driven LOW
const uint32_t I2C_CLOCK_HZ = 100000;  // slower than the glove's 400 kHz: more forgiving of a weak sensor
const uint16_t I2C_TIMEOUT_MS = 20;

const uint8_t REG_SMPLRT_DIV = 0x19, REG_CONFIG = 0x1A, REG_GYRO_CONFIG = 0x1B, REG_ACCEL_CONFIG = 0x1C;
const uint8_t REG_ACCEL_XOUT_H = 0x3B, REG_PWR_MGMT_1 = 0x6B, REG_WHO_AM_I = 0x75;
const float LSB_PER_G = 2048.0f;    // +-16 g, as the glove uses
const float LSB_PER_DPS = 16.4f;    // +-2000 deg/s
const int SAMPLES_PER_CHECK = 200;  // 1 s at 200 Hz

enum Health { NOT_FOUND, BAD_DATA, ALIVE };
volatile Health health = NOT_FOUND;
uint8_t mpuAddr = 0;

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

// Lists every address that answers. Returns how many did.
int scanBus(int sda, int scl, bool print) {
  Wire.end();
  Wire.begin(sda, scl, I2C_CLOCK_HZ);
  Wire.setTimeOut(I2C_TIMEOUT_MS);
  int found = 0;
  for (uint8_t a = 1; a < 127; a++) {
    Wire.beginTransmission(a);
    if (Wire.endTransmission() == 0) {
      found++;
      if (print) {
        Serial.printf("  device at 0x%02X%s\n", a, (a == 0x68 || a == 0x69) ? "  (an MPU address)" : "");
      }
      if (a == 0x68 || a == 0x69) {
        mpuAddr = a;
      }
    }
  }
  return found;
}

const char* chipName(int who) {
  switch (who) {
    case 0x68: return "MPU6050 (genuine)";
    case 0x70: return "MPU6500";
    case 0x71: return "MPU9250";
    case 0x73: return "MPU9255";
    case 0x72: return "MPU6050 clone (reads 0x72)";
    case 0x98: return "MPU6050 clone (reads 0x98)";
    case 0x19: return "MPU6886";
    case 0x00: case 0xFF: return "nothing sensible (stuck bus or dead chip)";
    default: return "unknown chip or clone";
  }
}

bool findSensor() {
  mpuAddr = 0;
  Serial.printf("\nScanning I2C on SDA GPIO%d, SCL GPIO%d...\n", MPU_SDA_PIN, MPU_SCL_PIN);
  int found = scanBus(MPU_SDA_PIN, MPU_SCL_PIN, true);
  if (found == 0) {
    Serial.println("  nothing answers. Trying with SDA and SCL swapped...");
    if (scanBus(MPU_SCL_PIN, MPU_SDA_PIN, false) > 0) {
      Serial.printf("  FOUND with the wires swapped: SDA is on GPIO%d and SCL on GPIO%d. Swap them back, or the\n"
                    "  glove firmware won't see the sensor.\n", MPU_SCL_PIN, MPU_SDA_PIN);
    } else {
      Serial.println("  still nothing. Check 3V3 and GND reach the sensor (its LED, if it has one, should be on),\n"
                     "  then SDA and SCL. If wiring is right, the sensor is dead.");
    }
    scanBus(MPU_SDA_PIN, MPU_SCL_PIN, false);
    mpuAddr = 0;
    return false;
  }
  if (mpuAddr == 0) {
    Serial.println("  something answers, but not at 0x68 or 0x69, where an MPU6050 lives.");
    return false;
  }
  int who = readRegister(REG_WHO_AM_I);
  Serial.printf("  WHO_AM_I = 0x%02X: %s\n", who & 0xFF, who < 0 ? "read failed" : chipName(who));
  bool ok = writeRegister(REG_PWR_MGMT_1, 0x01)      // wake up, gyro clock
         && writeRegister(REG_CONFIG, 0x01)          // 184 Hz low-pass
         && writeRegister(REG_SMPLRT_DIV, 0x04)      // 1 kHz / 5 = 200 Hz
         && writeRegister(REG_GYRO_CONFIG, 0x18)     // +-2000 deg/s
         && writeRegister(REG_ACCEL_CONFIG, 0x18);   // +-16 g
  delay(50);
  int pwr = readRegister(REG_PWR_MGMT_1), acc = readRegister(REG_ACCEL_CONFIG);
  if (!ok || pwr != 0x01 || acc != 0x18) {
    Serial.printf("  settings did not stick (wrote 0x01 and 0x18, read back 0x%02X and 0x%02X): the chip is not\n"
                  "  holding its registers, which usually means it is damaged.\n", pwr & 0xFF, acc & 0xFF);
    return false;
  }
  Serial.println("  set up as the glove uses it: +-16 g, +-2000 deg/s. Keep it STILL for the gravity check.\n");
  return true;
}

bool readRaw(int16_t* v) {  // ax ay az temp gx gy gz
  Wire.beginTransmission(mpuAddr);
  Wire.write(REG_ACCEL_XOUT_H);
  if (Wire.endTransmission(false) != 0 || Wire.requestFrom(mpuAddr, (uint8_t)14) != 14) {
    return false;
  }
  for (int i = 0; i < 7; i++) {
    uint8_t hi = Wire.read(), lo = Wire.read();
    v[i] = (int16_t)((hi << 8) | lo);
  }
  return true;
}

void ledTask(void*) {
  while (true) {
    switch (health) {
      case ALIVE:     digitalWrite(STATUS_LED_PIN, LOW); delay(60); digitalWrite(STATUS_LED_PIN, HIGH); delay(940); break;
      case BAD_DATA:  digitalWrite(STATUS_LED_PIN, LOW); delay(500); digitalWrite(STATUS_LED_PIN, HIGH); delay(500); break;
      default:        digitalWrite(STATUS_LED_PIN, LOW); delay(60); digitalWrite(STATUS_LED_PIN, HIGH); delay(60); break;
    }
  }
}

void setup() {
  pinMode(STATUS_LED_PIN, OUTPUT);
  digitalWrite(STATUS_LED_PIN, HIGH);
  Serial.begin(115200);
  delay(300);
  Serial.println("\n=== MPU6050 health check (ESP32-CAM, USB only) ===");
  xTaskCreatePinnedToCore(ledTask, "led", 2048, nullptr, 1, nullptr, 0);
}

void loop() {
  if (mpuAddr == 0 || health == NOT_FOUND) {
    health = NOT_FOUND;
    if (!findSensor()) {
      health = mpuAddr ? BAD_DATA : NOT_FOUND;
      Serial.println("VERDICT: NOT WORKING. Trying again in 3 s.");
      delay(3000);
      if (health == BAD_DATA) {
        health = NOT_FOUND;  // re-run the full search next time
      }
      return;
    }
  }

  if (readRegister(REG_PWR_MGMT_1) != 0x01 || readRegister(REG_ACCEL_CONFIG) != 0x18) {
    Serial.println("The sensor lost its settings (it reset itself: a power dip, a loose wire, or damage). "
                   "Setting it up again.");
    health = NOT_FOUND;
    return;
  }

  // One check: 200 readings over a second.
  int16_t v[7], first[7];
  double sum[7] = {0}, sq[7] = {0};
  int good = 0, failed = 0;
  bool changed[7] = {false};
  for (int n = 0; n < SAMPLES_PER_CHECK; n++) {
    if (readRaw(v)) {
      if (good == 0) {
        memcpy(first, v, sizeof(v));
      }
      for (int i = 0; i < 7; i++) {
        sum[i] += v[i];
        sq[i] += (double)v[i] * v[i];
        changed[i] |= v[i] != first[i];
      }
      good++;
    } else {
      failed++;
    }
    delay(5);
  }

  if (good == 0) {
    Serial.println("VERDICT: NOT WORKING. It answered at first, but every reading failed. Searching again.");
    health = NOT_FOUND;
    return;
  }
  float mean[7], sd[7];
  for (int i = 0; i < 7; i++) {
    mean[i] = sum[i] / good;
    sd[i] = sqrt(fmax(0.0, sq[i] / good - mean[i] * mean[i]));
  }
  float ax = mean[0] / LSB_PER_G, ay = mean[1] / LSB_PER_G, az = mean[2] / LSB_PER_G;
  float g = sqrt(ax * ax + ay * ay + az * az);
  float gx = mean[4] / LSB_PER_DPS, gy = mean[5] / LSB_PER_DPS, gz = mean[6] / LSB_PER_DPS;
  float spin = sqrt(gx * gx + gy * gy + gz * gz);
  float tempC = mean[3] / 340.0f + 36.53f;
  float noiseG = sqrt(sd[0] * sd[0] + sd[1] * sd[1] + sd[2] * sd[2]) / LSB_PER_G;

  Serial.printf("accel %+6.2f %+6.2f %+6.2f g  |a| %.2f g  noise %.3f g | gyro %+7.1f %+7.1f %+7.1f deg/s | %4.1f C | "
                "%d ok, %d failed\n", ax, ay, az, g, noiseG, gx, gy, gz, tempC, good, failed);

  bool accelMoves = changed[0] || changed[1] || changed[2];
  bool gyroMoves = changed[4] || changed[5] || changed[6];
  bool allZero = mean[0] == 0 && mean[1] == 0 && mean[2] == 0 && mean[4] == 0 && mean[5] == 0 && mean[6] == 0;
  bool allMinus1 = mean[0] == -1 && mean[1] == -1 && mean[2] == -1 && mean[4] == -1;
  String problems = "";
  if (failed > SAMPLES_PER_CHECK / 20) problems += " I2C reads failing (" + String(failed) + " of " + String(SAMPLES_PER_CHECK) + ");";
  if (allZero) problems += " every reading is 0 (dead or asleep);";
  else if (allMinus1) problems += " every reading is -1 (bus stuck high: dead chip or bad wire);";
  else {
    if (!accelMoves) problems += " accelerometer frozen (never changes);";
    if (!gyroMoves) problems += " gyro frozen (never changes);";
  }
  if (accelMoves && (g < 0.8f || g > 1.2f) && spin < 20) {
    problems += " gravity reads " + String(g, 2) + " g at rest (should be about 1.00 g);";
  }
  if (gyroMoves && spin > 15 && noiseG < 0.05f) {
    problems += " gyro reads " + String(spin, 0) + " deg/s while still (should be under about 5);";
  }
  if (tempC < -10 || tempC > 85) problems += " temperature " + String(tempC, 0) + " C is not believable;";

  if (problems.length() == 0) {
    health = ALIVE;
    Serial.println("VERDICT: ALIVE. Move it: the numbers should change, and |a| should jump above 1 g.");
  } else {
    health = BAD_DATA;
    Serial.println("VERDICT: PROBLEM:" + problems + (noiseG > 0.05f ? " (it was moving, so keep it still and wait)" : ""));
  }
  if (failed == SAMPLES_PER_CHECK) {
    health = NOT_FOUND;
  }
}

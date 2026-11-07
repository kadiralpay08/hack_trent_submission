/*
 * MPU-9250 / 9255 / 6500 HEALTH CHECK for the ESP32-S3 glove. USB serial only: no WiFi, nothing to configure.
 * --------------------------------------------------------------------------------------------------------
 * Is the sensor alive, and is its magnetometer working? Flash this, open Serial Monitor at 115200 baud,
 * and read the verdict. Flash the real glove firmware (firmware/glove_imu_s3) again afterwards.
 *
 * BOARD: "ESP32S3 Dev Module", with Tools > USB CDC On Boot = Enabled when plugged into the S3's own USB
 * port (FQBN esp32:esp32:esp32s3:CDCOnBoot=cdc). Without that, nothing prints on that port.
 * WIRING: the same as the glove: VCC -> 3V3, GND -> GND, SDA -> GPIO2, SCL -> GPIO1, AD0 -> GND.
 *
 * WHAT IT CHECKS, every second (200 readings):
 *   1. Something answers on I2C (it also tries SDA and SCL swapped, to catch crossed wires).
 *   2. WHO_AM_I: 0x71 = MPU-9250, 0x73 = MPU-9255, 0x70 = MPU-6500 (no magnetometer).
 *   3. Accel and gyro read without errors, move, and at rest show about 1 g and about 0 deg/s. Keep it STILL.
 *   4. The AK8963 magnetometer answers (WIA = 0x48), sends fresh readings at about 100 Hz, does not
 *      overflow, and its field strength is believable (the Earth's field is about 25 to 65 uT).
 * Turn the board slowly: the magnetometer numbers should swing, while |B| stays roughly the same.
 *
 * LED: none is used, because S3 boards put their LEDs on different pins.
 */

#include <Wire.h>

// ---------------- USER CONFIGURATION ----------------
int MPU_SDA_PIN = 2;
int MPU_SCL_PIN = 1;
// ------------------------------------------------------

const uint8_t MAG_ADDR = 0x0C;
const uint32_t I2C_CLOCK_HZ = 100000;  // slower than the glove's 400 kHz: more forgiving
const uint16_t I2C_TIMEOUT_MS = 20;
const float LSB_PER_G = 2048.0f, LSB_PER_DPS = 16.4f, UT_PER_LSB = 0.15f;
const int SAMPLES_PER_CHECK = 200;  // 1 s at 200 Hz

uint8_t mpuAddr = 0;
bool magPresent = false;
float asa[3] = {1, 1, 1};

bool writeReg(uint8_t addr, uint8_t reg, uint8_t value) {
  Wire.beginTransmission(addr);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission() == 0;
}

bool readRegs(uint8_t addr, uint8_t reg, uint8_t* out, uint8_t n) {
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

int readReg(uint8_t addr, uint8_t reg) {
  uint8_t v;
  return readRegs(addr, reg, &v, 1) ? v : -1;
}

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
        Serial.printf("  device at 0x%02X%s\n", a, (a == 0x68 || a == 0x69) ? "  (an MPU address)"
                                                    : a == MAG_ADDR ? "  (AK8963 magnetometer)" : "");
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
    case 0x71: return "MPU-9250 (has a magnetometer)";
    case 0x73: return "MPU-9255 (has a magnetometer)";
    case 0x70: return "MPU-6500 (NO magnetometer: often sold as a 9250)";
    case 0x68: return "MPU6050 (no magnetometer)";
    case 0x00: case 0xFF: return "nothing sensible (stuck bus or dead chip)";
    default: return "unknown chip or clone";
  }
}

bool setupMag() {
  magPresent = false;
  writeReg(mpuAddr, 0x6A, 0x00);  // USER_CTRL: the MPU's own I2C master off
  writeReg(mpuAddr, 0x37, 0x02);  // INT_PIN_CFG: bypass, so the AK8963 sits on our bus
  delay(10);
  int wia = readReg(MAG_ADDR, 0x00);
  if (wia != 0x48) {
    Serial.printf("  magnetometer: no AK8963 at 0x0C (WIA read %d, expected 0x48)\n", wia);
    return false;
  }
  uint8_t raw[3];
  writeReg(MAG_ADDR, 0x0A, 0x00);  // power down
  delay(10);
  writeReg(MAG_ADDR, 0x0A, 0x0F);  // fuse ROM mode, to read the factory adjustment
  delay(10);
  if (readRegs(MAG_ADDR, 0x10, raw, 3)) {
    for (int i = 0; i < 3; i++) {
      asa[i] = (raw[i] - 128) / 256.0f + 1.0f;
    }
  }
  writeReg(MAG_ADDR, 0x0A, 0x00);
  delay(10);
  writeReg(MAG_ADDR, 0x0A, 0x16);  // 16-bit, continuous 100 Hz
  delay(10);
  Serial.printf("  magnetometer: AK8963 found, factory adjustment %.2f %.2f %.2f\n", asa[0], asa[1], asa[2]);
  magPresent = true;
  return true;
}

bool findSensor() {
  mpuAddr = 0;
  Serial.printf("\nScanning I2C on SDA GPIO%d, SCL GPIO%d...\n", MPU_SDA_PIN, MPU_SCL_PIN);
  if (scanBus(MPU_SDA_PIN, MPU_SCL_PIN, true) == 0) {
    Serial.println("  nothing answers. Trying with SDA and SCL swapped...");
    if (scanBus(MPU_SCL_PIN, MPU_SDA_PIN, false) > 0) {
      Serial.printf("  FOUND with the wires swapped: SDA is on GPIO%d and SCL on GPIO%d. Swap them, or change\n"
                    "  MPU_SDA_PIN and MPU_SCL_PIN in the glove sketch.\n", MPU_SCL_PIN, MPU_SDA_PIN);
    } else {
      Serial.println("  still nothing. Check 3V3 and GND reach the sensor, then SDA and SCL.");
    }
    scanBus(MPU_SDA_PIN, MPU_SCL_PIN, false);
    mpuAddr = 0;
    return false;
  }
  if (mpuAddr == 0) {
    Serial.println("  something answers, but not at 0x68 or 0x69, where the MPU lives.");
    return false;
  }
  int who = readReg(mpuAddr, 0x75);
  Serial.printf("  WHO_AM_I = 0x%02X: %s\n", who & 0xFF, who < 0 ? "read failed" : chipName(who));
  bool ok = writeReg(mpuAddr, 0x6B, 0x01)    // wake, best clock
         && writeReg(mpuAddr, 0x1A, 0x01)    // 184 Hz low-pass
         && writeReg(mpuAddr, 0x19, 0x04)    // 1 kHz / 5 = 200 Hz
         && writeReg(mpuAddr, 0x1B, 0x18)    // +-2000 deg/s
         && writeReg(mpuAddr, 0x1C, 0x18);   // +-16 g
  delay(50);
  int pwr = readReg(mpuAddr, 0x6B), acc = readReg(mpuAddr, 0x1C);
  if (!ok || pwr != 0x01 || acc != 0x18) {
    Serial.printf("  settings did not stick (read back 0x%02X and 0x%02X): the chip may be damaged.\n", pwr & 0xFF, acc & 0xFF);
    return false;
  }
  setupMag();
  Serial.println("  set up as the glove uses it: +-16 g, +-2000 deg/s. Keep it STILL for the gravity check.\n");
  return true;
}

void setup() {
  Serial.begin(115200);
  uint32_t t0 = millis();
  while (!Serial && millis() - t0 < 3000) {
    delay(10);
  }
  Serial.println("\n=== MPU-9250 health check (ESP32-S3, USB only) ===");
}

void loop() {
  if (mpuAddr == 0) {
    if (!findSensor()) {
      Serial.println("VERDICT: NOT WORKING. Trying again in 3 s.");
      mpuAddr = 0;
      delay(3000);
      return;
    }
  }
  if (readReg(mpuAddr, 0x6B) != 0x01 || readReg(mpuAddr, 0x1C) != 0x18) {
    Serial.println("The sensor lost its settings (it reset itself: a power dip, a loose wire, or damage). Setting it up again.");
    mpuAddr = 0;
    return;
  }

  double sum[7] = {0}, sq[7] = {0}, msum[3] = {0};
  int16_t first[7];
  bool changed[7] = {false}, mchanged = false;
  int good = 0, failed = 0, magFresh = 0, magOverflow = 0, magFail = 0;
  float mfirst[3] = {0}, mlast[3] = {0};
  for (int n = 0; n < SAMPLES_PER_CHECK; n++) {
    uint8_t raw[14];
    if (readRegs(mpuAddr, 0x3B, raw, 14)) {
      int16_t v[7];
      for (int i = 0; i < 7; i++) {
        v[i] = (int16_t)((raw[2 * i] << 8) | raw[2 * i + 1]);
      }
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
    if (magPresent) {
      int st1 = readReg(MAG_ADDR, 0x02);
      if (st1 < 0) {
        magFail++;
      } else if (st1 & 0x01) {
        uint8_t m[7];
        if (readRegs(MAG_ADDR, 0x03, m, 7)) {
          if (m[6] & 0x08) {
            magOverflow++;
          } else {
            for (int i = 0; i < 3; i++) {
              float ut = (int16_t)((m[2 * i + 1] << 8) | m[2 * i]) * asa[i] * UT_PER_LSB;
              if (magFresh == 0) mfirst[i] = ut;
              mchanged |= magFresh > 0 && ut != mlast[i];
              mlast[i] = ut;
              msum[i] += ut;
            }
            magFresh++;
          }
        } else {
          magFail++;
        }
      }
    }
    delay(5);
  }

  if (good == 0) {
    Serial.println("VERDICT: NOT WORKING. It answered at first, but every reading failed. Searching again.");
    mpuAddr = 0;
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
  float tempC = mean[3] / 333.87f + 21.0f;  // MPU-9250 temperature formula
  float noiseG = sqrt(sd[0] * sd[0] + sd[1] * sd[1] + sd[2] * sd[2]) / LSB_PER_G;

  Serial.printf("accel %+6.2f %+6.2f %+6.2f g |a| %.2f | gyro %+7.1f %+7.1f %+7.1f dps | %4.1f C | %d ok %d failed\n",
                ax, ay, az, g, gx, gy, gz, tempC, good, failed);
  float bx = 0, by = 0, bz = 0, b = 0;
  if (magPresent && magFresh) {
    bx = msum[0] / magFresh; by = msum[1] / magFresh; bz = msum[2] / magFresh;
    b = sqrt(bx * bx + by * by + bz * bz);
    Serial.printf("  mag %+7.1f %+7.1f %+7.1f uT |B| %.1f uT | %d fresh readings/s, %d overflow, %d failed\n",
                  bx, by, bz, b, magFresh, magOverflow, magFail);
  }

  String problems = "", notes = "";
  bool allZero = mean[0] == 0 && mean[1] == 0 && mean[2] == 0 && mean[4] == 0 && mean[5] == 0 && mean[6] == 0;
  if (failed > SAMPLES_PER_CHECK / 20) problems += " accel/gyro reads failing (" + String(failed) + ");";
  if (allZero) problems += " every reading is 0 (dead or asleep);";
  else {
    if (!(changed[0] || changed[1] || changed[2])) problems += " accelerometer frozen;";
    if (!(changed[4] || changed[5] || changed[6])) problems += " gyro frozen;";
  }
  if ((g < 0.8f || g > 1.2f) && spin < 20) problems += " gravity reads " + String(g, 2) + " g at rest (should be about 1.00);";
  if (spin > 15 && noiseG < 0.05f) problems += " gyro reads " + String(spin, 0) + " deg/s while still;";
  if (magPresent) {
    if (magFresh < 40) problems += " magnetometer too slow (" + String(magFresh) + " fresh/s, expected about 100);";
    else if (!mchanged) problems += " magnetometer frozen;";
    if (magOverflow > magFresh / 10) problems += " magnetometer overflowing (a magnet or motor nearby?);";
    if (magFresh && (b < 10 || b > 150)) notes += " field " + String(b, 0) + " uT is unusual for Earth's (25-65): metal or magnets nearby, or it needs calibrating;";
  } else {
    notes += " no magnetometer: the glove still works, without the imu_mag features;";
  }

  if (problems.length() == 0) {
    Serial.println("VERDICT: ALIVE." + (notes.length() ? String(" Note:") + notes : String("")));
  } else {
    Serial.println("VERDICT: PROBLEM:" + problems + (noiseG > 0.05f ? " (it was moving: keep it still)" : ""));
  }
}

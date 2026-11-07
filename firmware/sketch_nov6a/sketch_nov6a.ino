/*
 * MPU6050 -> Arduino Uno -> Relative Angles on the Serial Plotter
 * ---------------------------------------------------------------
 * LIBRARIES NEEDED:
 *   - Wire.h  (built into the Arduino IDE, no install needed)
 *   This sketch talks to the MPU6050 directly over I2C registers, so no
 *   external MPU6050 or Kalman library needs to be installed.
 *
 * WIRING:
 *   MPU6050 VCC -> Arduino 5V (or 3.3V, check your board)
 *   MPU6050 GND -> Arduino GND
 *   MPU6050 SCL -> Arduino A5 (Uno hardware I2C clock pin)
 *   MPU6050 SDA -> Arduino A4 (Uno hardware I2C data pin)
 *   (On an Uno, I2C pins are fixed to A4/A5, so they are not configurable.)
 *
 * USAGE:
 *   Upload this sketch, then open Tools > Serial Plotter (or Serial Monitor)
 *   and set the baud rate to match SERIAL_BAUD below.
 *   Output is Roll, Pitch and Yaw in degrees, from -180 to 180.
 *
 * CALIBRATION:
 *   Keep the sensor still for about 1 second after every reset. Its position
 *   at that moment becomes 0,0,0.
 *
 * ACCURACY LIMITS:
 *   - Roll and Pitch use a Kalman filter that combines the gyro with gravity
 *     measured by the accelerometer, so they stay stable over time.
 *   - Yaw is integrated from the gyro only and WILL slowly drift. Rotating
 *     around the vertical axis does not change the gravity reading, so yaw has
 *     no reference to correct it. Fixing this requires a magnetometer.
 *   - Roll and Pitch are unreliable when Pitch is close to +-90 degrees.
 *   - Rotation faster than 250 deg/s exceeds the gyro range and causes error.
 */

#include <Arduino.h>
#include <Wire.h>

// ---------------- USER CONFIGURATION ----------------
#define MPU6050_ADDR 0x68       // change to 0x69 if AD0 pin is pulled high
#define SERIAL_BAUD 115200      // must match Serial Plotter baud rate
#define CALIBRATION_SAMPLES 200 // readings averaged to find the starting position
#define YAW_RATE_THRESHOLD 0.5  // yaw rates below this are ignored, in deg/s

// Kalman tuning:
//   Raise Q_ANGLE to trust the gyro less and respond faster, at the cost of noise.
//   Raise R_MEASURE to trust the accelerometer less, for a smoother output.
#define Q_ANGLE 0.001
#define Q_BIAS 0.003
#define R_MEASURE 0.03
// ------------------------------------------------------

// MPU6050 register addresses
#define REG_PWR_MGMT_1   0x6B
#define REG_ACCEL_XOUT_H 0x3B

// Sensitivity scale factors for default full-scale ranges:
//   Accel: +-2g  -> 16384 LSB/g
//   Gyro:  +-250 deg/s -> 131 LSB/(deg/s)
const float ACCEL_SCALE = 16384.0;
const float GYRO_SCALE  = 131.0;

struct Kalman {
  float angle;   // estimated angle, in degrees
  float bias;    // estimated gyro bias, in deg/s
  float P[2][2]; // error covariance
};

Kalman kalmanRoll, kalmanPitch;

float rollStart, pitchStart;
float yaw = 0;
float gyroXOffset, gyroYOffset, gyroZOffset; // in deg/s
unsigned long lastMicros;

float wrapAngle(float angle) {
  while (angle > 180.0) angle -= 360.0;
  while (angle < -180.0) angle += 360.0;
  return angle;
}

// Zeroes out values whose magnitude is below threshold (noise rejection)
float applyDeadzone(float value, float threshold) {
  if (value > -threshold && value < threshold) {
    return 0.0;
  }
  return value;
}

void kalmanInit(Kalman &k, float startAngle) {
  k.angle = startAngle;
  k.bias = 0;
  k.P[0][0] = 0;
  k.P[0][1] = 0;
  k.P[1][0] = 0;
  k.P[1][1] = 0;
}

void kalmanUpdate(Kalman &k, float measuredAngle, float gyroRate, float dt) {
  // Predict: advance the angle using the gyro
  k.angle += dt * (gyroRate - k.bias);
  k.P[0][0] += dt * (dt * k.P[1][1] - k.P[0][1] - k.P[1][0] + Q_ANGLE);
  k.P[0][1] -= dt * k.P[1][1];
  k.P[1][0] -= dt * k.P[1][1];
  k.P[1][1] += Q_BIAS * dt;

  // Update: correct the prediction using the accelerometer angle
  float S = k.P[0][0] + R_MEASURE;
  float K0 = k.P[0][0] / S;
  float K1 = k.P[1][0] / S;
  float error = wrapAngle(measuredAngle - k.angle);

  k.angle = wrapAngle(k.angle + K0 * error);
  k.bias += K1 * error;

  float P00 = k.P[0][0];
  float P01 = k.P[0][1];
  k.P[0][0] -= K0 * P00;
  k.P[0][1] -= K0 * P01;
  k.P[1][0] -= K1 * P00;
  k.P[1][1] -= K1 * P01;
}

int16_t readInt16() {
  uint8_t high = Wire.read();
  uint8_t low = Wire.read();
  return (int16_t)((high << 8) | low);
}

// Reads accel and gyro together so all six values come from the same instant
void readSensor(float &ax, float &ay, float &az,
                float &gx, float &gy, float &gz) {
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_ACCEL_XOUT_H);
  Wire.endTransmission(false);
  Wire.requestFrom(MPU6050_ADDR, 14, true);

  ax = readInt16() / ACCEL_SCALE;
  ay = readInt16() / ACCEL_SCALE;
  az = readInt16() / ACCEL_SCALE;
  readInt16(); // skip the temperature reading between accel and gyro
  gx = readInt16() / GYRO_SCALE;
  gy = readInt16() / GYRO_SCALE;
  gz = readInt16() / GYRO_SCALE;
}

float accelRoll(float ay, float az) {
  return atan2(ay, az) * RAD_TO_DEG;
}

float accelPitch(float ax, float ay, float az) {
  return atan2(-ax, sqrt(ay * ay + az * az)) * RAD_TO_DEG;
}

void calibrate() {
  float sumAx = 0, sumAy = 0, sumAz = 0;
  float sumGx = 0, sumGy = 0, sumGz = 0;
  float ax, ay, az, gx, gy, gz;

  for (int i = 0; i < CALIBRATION_SAMPLES; i++) {
    readSensor(ax, ay, az, gx, gy, gz);
    sumAx += ax;
    sumAy += ay;
    sumAz += az;
    sumGx += gx;
    sumGy += gy;
    sumGz += gz;
    delay(5);
  }

  gyroXOffset = sumGx / CALIBRATION_SAMPLES;
  gyroYOffset = sumGy / CALIBRATION_SAMPLES;
  gyroZOffset = sumGz / CALIBRATION_SAMPLES;

  // Accel keeps its gravity reading, since gravity is what gives the tilt angle
  rollStart = accelRoll(sumAy, sumAz);
  pitchStart = accelPitch(sumAx, sumAy, sumAz);

  kalmanInit(kalmanRoll, rollStart);
  kalmanInit(kalmanPitch, pitchStart);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  Wire.begin();

  // Wake up the MPU6050 (it starts in sleep mode)
  Wire.beginTransmission(MPU6050_ADDR);
  Wire.write(REG_PWR_MGMT_1);
  Wire.write(0x00);
  Wire.endTransmission();

  delay(100);

  calibrate();
  lastMicros = micros();
}

void loop() {
  float ax, ay, az, gx, gy, gz;
  readSensor(ax, ay, az, gx, gy, gz);

  unsigned long now = micros();
  float dt = (now - lastMicros) / 1000000.0;
  lastMicros = now;

  kalmanUpdate(kalmanRoll, accelRoll(ay, az), gx - gyroXOffset, dt);
  kalmanUpdate(kalmanPitch, accelPitch(ax, ay, az), gy - gyroYOffset, dt);

  // Yaw has no accelerometer measurement, so the Kalman update has nothing to use
  yaw = wrapAngle(yaw + applyDeadzone(gz - gyroZOffset, YAW_RATE_THRESHOLD) * dt);

  // Labeled, comma-separated output for the Serial Plotter
  Serial.print("Roll:");
  Serial.print(wrapAngle(kalmanRoll.angle - rollStart), 2);
  Serial.print(",Pitch:");
  Serial.print(wrapAngle(kalmanPitch.angle - pitchStart), 2);
  Serial.print(",Yaw:");
  Serial.println(yaw, 2);

  delay(20); // ~50 Hz update rate
}

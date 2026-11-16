/*
 * Exhaustive study of the Limbo result formula.
 *
 * A Limbo round uses 4 bytes of an HMAC-SHA256 digest, so there are exactly
 * 2^32 equally likely draws. This program evaluates the formula for every one
 * of them (the same IEEE-754 double operations as the JavaScript) and reports:
 *
 *   - whether the result ever increases as the draw grows (it must not: the
 *     exact-odds code in src/limbo-core.js relies on that),
 *   - the exact number of winning draws for a list of targets,
 *   - how many draws give exactly 1.00x (rounds nobody can win),
 *   - how often the formula printed in the public documentation disagrees with
 *     the formula the verification code really uses.
 *
 * Build:  gcc -O2 -fopenmp -ffp-contract=off -o exact_enum exact_enum.c -lm
 * Run:    ./exact_enum
 */
#include <math.h>
#include <stdint.h>
#include <stdio.h>

static inline double real_formula(uint32_t k) {
  double f = (double)k / 4294967296.0;
  double float_point = 1e8 / (floor(f * 1e8) + 1.0) * 0.99;
  double crash_point = floor(float_point * 100.0) / 100.0;
  return crash_point < 1.0 ? 1.0 : crash_point;
}

static inline double documented_formula(uint32_t k) {
  double f = (double)k / 4294967296.0;
  double float_point = 1e8 / (f * 1e8) * 0.99;
  double crash_point = floor(float_point * 100.0) / 100.0;
  return crash_point < 1.0 ? 1.0 : crash_point;
}

#define NT 14
static const double TARGETS[NT] = {1.01, 1.10, 1.50, 2.0,    3.0,     5.0,      10.0,
                                   50.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0, 99000000.0};

int main(void) {
  const uint64_t N = 4294967296ULL;
  const uint64_t CHUNK = 1ULL << 22;
  const int64_t chunks = (int64_t)(N / CHUNK);

  uint64_t wins[NT] = {0};
  uint64_t increases = 0, ones = 0, doc_mismatch = 0, doc_mismatch_below_1000 = 0;
  uint64_t doc_higher = 0, doc_lower = 0;
  double sum = 0.0;

#pragma omp parallel for schedule(dynamic, 4) \
    reduction(+ : wins[:NT], increases, ones, doc_mismatch, doc_mismatch_below_1000, doc_higher, doc_lower, sum)
  for (int64_t c = 0; c < chunks; c++) {
    uint64_t start = (uint64_t)c * CHUNK;
    double prev = start == 0 ? INFINITY : real_formula((uint32_t)(start - 1));
    for (uint64_t i = start; i < start + CHUNK; i++) {
      uint32_t k = (uint32_t)i;
      double m = real_formula(k);
      double d = documented_formula(k);
      if (m > prev) increases++;
      prev = m;
      if (m == 1.0) ones++;
      if (m != d) {
        doc_mismatch++;
        if (m < 1000.0) doc_mismatch_below_1000++;
        if (d > m) doc_higher++; else doc_lower++;
      }
      for (int t = 0; t < NT; t++)
        if (m >= TARGETS[t]) wins[t]++;
      sum += m;
    }
  }

  printf("draws                         %llu\n", (unsigned long long)N);
  printf("increases (must be 0)         %llu\n", (unsigned long long)increases);
  printf("results equal to 1.00         %llu  (%.6f%%)\n", (unsigned long long)ones, 100.0 * ones / N);
  printf("mean result                   %.6f\n", sum / N);
  printf("docs formula != real formula  %llu  (%.6f%%)\n", (unsigned long long)doc_mismatch,
         100.0 * doc_mismatch / N);
  printf("  of which result < 1000      %llu\n", (unsigned long long)doc_mismatch_below_1000);
  printf("  docs higher / docs lower    %llu / %llu\n", (unsigned long long)doc_higher,
         (unsigned long long)doc_lower);
  printf("\n%-12s %-12s %-14s %-12s %s\n", "target", "win draws", "win chance %", "RTP", "house edge %");
  for (int t = 0; t < NT; t++) {
    double p = (double)wins[t] / N;
    printf("%-12.2f %-12llu %-14.8f %-12.8f %.6f\n", TARGETS[t], (unsigned long long)wins[t], 100.0 * p,
           p * TARGETS[t], 100.0 * (1.0 - p * TARGETS[t]));
  }
  return 0;
}

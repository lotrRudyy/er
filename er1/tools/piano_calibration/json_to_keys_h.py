import json
import sys

MAX_HARM = 10

j = json.load(open("model_v3_with_keys.json"))

keys = j["keys"]

print("#pragma once")
print("")
print("struct KeyModel {")
print("  int key_idx;")
print("  float f0_med_hz;")
print("  float f0_mad_cents;")
print("  float harm_vec_med[%d];" % MAX_HARM)
print("};")
print("")
print("static const KeyModel gKeys[] = {")

for k, v in sorted(keys.items(), key=lambda x: int(x[0])):
    hv = v["harm_vec_med"]
    hv = hv + [0.0] * (MAX_HARM - len(hv))
    hv_str = ", ".join(f"{x:.6f}f" for x in hv)

    print("  { %d, %.6ff, %.6ff, { %s } }," % (
        int(k),
        v["f0_med_hz"],
        v["f0_mad_cents"],
        hv_str
    ))

print("};")
print("")
print("static const int gNumKeys = sizeof(gKeys) / sizeof(gKeys[0]);")

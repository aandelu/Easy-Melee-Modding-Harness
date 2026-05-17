# Local Spot Dodge Macro (Reaction Test) - Hardware Pad Hook

This iteration bypasses the Game Engine's global floats completely to ensure there is **zero risk** of memory corruption or joystick breakage. 

Instead of overwriting Melee's internal arrays, we are intercepting the raw 8-bit `PADStatus` hardware buffer directly from the Gamecube controller hardware exactly 1 microsecond before the Melee engine reads it. By writing `-127` directly into the hardware buffer's `StickY` slot, we trick the game engine into doing all the floating-point deadzone math itself!

**Scenario:**
- **Port 1:** Opponent (e.g. Marth) - Press `Z` to Grab.
- **Port 2:** Local Player / CPU (e.g. Fox) - Will automatically spot dodge on the same frame.

### The Gecko Code

```text
$Spot Dodge on Opponent Grab (Hardware Pad Hook)
*Triggers Spot Dodge on Port 2 when Port 1 presses Z.
C23775C0 00000007
2C180001 4082002C
A019FFF4 70000010
41820020 A0190000
60000040 B0190000
38000081 98190003
380000FF 98190006
88190002 60000000
```

### PowerPC Assembly Source

```assembly
# Hook Address: 0x803775C0 (Pad Process Loop)
# r24 = Current Port Index (0 = P1, 1 = P2)
# r25 = Pointer to current port's raw PADStatus hardware struct
# Original Instruction: lbz r0, 2(r25) [Loads Stick X]

# 1. Check if we are processing Port 2
cmpwi r24, 1            # Are we processing Port 2?
bne _end                # If not, jump to _end

# 2. Check Port 1's Hardware Inputs (12 bytes prior to Port 2)
lhz r0, -12(r25)        # Load Port 1 Digital Buttons
andi. r0, r0, 0x0010    # Check if Z button (0x0010) is pressed
beq _end                # If not, jump to _end

# 3. Spoof Port 2's Hardware Inputs
lhz r0, 0(r25)          # Load Port 2's Digital Buttons
ori r0, r0, 0x0040      # Add L Button mask (0x0040)
sth r0, 0(r25)          # Store it back

li r0, 0x81             # Int: -127 (Max Down)
stb r0, 3(r25)          # Overwrite Stick Y (offset 3)

li r0, 0xFF             # Int: 255 (Max Press)
stb r0, 6(r25)          # Overwrite L Trigger (offset 6)

_end:
# 4. Execute Original Instruction & Safe Padding
lbz r0, 2(r25)          # 88 19 00 02
nop                     # 60 00 00 00 (Prevents crash if code blocks align weirdly)
```
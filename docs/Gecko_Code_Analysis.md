# SSBM Gecko Code Analysis

This document contains disassembled PowerPC assembly and documentation for various Super Smash Bros. Melee Gecko codes, particularly focusing on mechanics useful for online-safe macros.

## 1. Swap X/Z - Netplay Safe [Altimor]
**Original Code:**
```text
C234E2AC 00000002
5000843E 5000B56A
500056F6 00000000
```

**Disassembly (`0x8034E2AC`):**
```assembly
0x8034e2ac: rlwimi r0, r0, 0x10, 0x10, 0x1f
0x8034e2b0: rlwimi r0, r0, 0x16, 0x15, 0x15
0x8034e2b4: rlwimi r0, r0, 0xa, 0x1b, 0x1b
```
**Notes:** 
This code uses `rlwimi` (Rotate Left Word Immediate then Mask Insert) to directly swap the bitwise representations of the X and Z buttons in the controller input register. It acts specifically on the local player's input before it gets processed by the game engine, making it safe for netplay.

---

## 2. X + Y = Short Hop Macro [UnclePunch]
**Original Code:**
```text
C20CB60C 00000005
809F002C 8064065C
54600529 41820014
5460056B 4182000C
38600001 90642340
7C7F1B78 00000000
```

**Disassembly (`0x800CB60C`):**
```assembly
0x800cb60c: lwz     r4, 0x2c(r31)     # Load Player Data pointer
0x800cb610: lwz     r3, 0x65c(r4)     # Load current button inputs
0x800cb614: rlwinm. r0, r3, 0, 0x14, 0x14 # Check if X is held
0x800cb618: beq     0x800cb62c        # If not held, skip macro
0x800cb61c: rlwinm. r0, r3, 0, 0x15, 0x15 # Check if Y is held
0x800cb620: beq     0x800cb62c        # If not held, skip macro
0x800cb624: li      r3, 1             # Load '1'
0x800cb628: stw     r3, 0x2340(r4)    # Store into Short Hop trigger flag in Player Data
0x800cb62c: mr      r31, r3           # Original instruction replacement
```
**Notes:** 
A prime example of checking specific button combinations (`r3, 0x65c(r4)`) from the Player Data structure and setting an internal flag (`0x2340`) to force an action state transition (in this case, short hop during jumpsquat).

---

## 3. Flash Red on Failed L-Cancel [Achilles1515, Fizzi]
**Original Code:**
*(See full Gecko code string in related HTML/source)*

### Part A: Trigger Logic & Netplay Check (`0x8008D690`)
```assembly
# --- Slippi Netplay Check ---
0x8008d690: lis    r7, 0x8048       # Load upper half of global state address
0x8008d694: lwz    r7, 0x9d30(r7)   # Load game state/scene ID from 0x80489D30
0x8008d698: rlwinm r7, r7, 8, 16, 31 # Extract specific bits (current minor scene)
0x8008d69c: cmpwi  r7, 0x208        # Is this an online match? (0x208 = Slippi online scene)
0x8008d6a0: bne    0x8008d6c0       # If NOT online, skip the local port check entirely

# --- Local Port Verification ---
0x8008d6a4: lwz    r7, -0x49e4(r13) # r13 points to SDA. Offset -0x49E4 holds pointer to local port ID
0x8008d6a8: lbz    r7, 0(r7)        # Load the local player's port ID (0-3)
0x8008d6ac: lbz    r8, 0xc(r5)      # Load the port ID of the character currently being processed (r5 = Player Data)
0x8008d6b0: cmpw   r7, r8           # Compare local port vs current character's port
0x8008d6b4: beq    0x8008d6c0       # If they match, this is OUR character. Proceed to check L-Cancel.
0x8008d6b8: lbz    r5, 0x67f(r5)    # Otherwise, execute original instruction...
0x8008d6bc: b      0x8008d6d4       # ...and exit. (Don't affect opponent's character!)

# --- L-Cancel Failure Check ---
0x8008d6c0: lbz    r5, 0x67f(r5)    # Original instruction: Load L-cancel status/timer from Player Data
0x8008d6c4: cmpwi  r5, 7            # Check if L-cancel timer missed the window (< 7 means success usually)
0x8008d6c8: blt    0x8008d6d4       # If successful (timer < 7), skip flashing red
0x8008d6cc: li     r15, 0xd4        # Load our custom "Flash Red" trigger flag (0xD4)
0x8008d6d0: stb    r15, 0x564(r3)   # Store it into the color overlay timer in Player Data
```

### Part B: Color Modification Logic (`0x800C0148`)
```assembly
0x800c0148: addi r3, r31, 0x488   # Original instruction being replaced
0x800c014c: lbz  r15, 0x564(r30)  # Load the color overlay flag/timer from player data
0x800c0150: cmpwi r15, 0xd4       # Check if our custom "flash red" flag (0xD4) is set
0x800c0154: beq  0x800c015c       # If it is, jump to the color modification code
0x800c0158: b    0x800c01a4       # Otherwise, exit the injection

# --- Color Modification ---
0x800c015c: li   r15, 0x91        # Load the normal "damage flash" ID
0x800c0160: stb  r15, 0x564(r30)  # Store it back into the color flag to trigger the effect
0x800c0164: lis  r15, 0x437f      # Load RGB value for Red (floating point representation)
0x800c0168: stw  r15, 0x518(r30)  # Store Red color channel
0x800c016c: lis  r15, -0x3e00     # Load RGB value for Green/Blue modifiers
0x800c0170: stw  r15, 0x524(r30)  # Store color channel data
0x800c0174: lis  r15, 0           # Load 0 for clearing other color channels
0x800c0178: stw  r15, 0x51c(r30)  
0x800c017c: stw  r15, 0x520(r30)  
0x800c0180: stw  r15, 0x528(r30)  
0x800c0184: stw  r15, 0x52c(r30)  
0x800c0188: stw  r15, 0x530(r30)  
0x800c018c: lis  r15, -0x3d80     # Load opacity/alpha value
0x800c0190: stw  r15, 0x534(r30)  # Store Alpha
0x800c0194: lis  r15, 0x800c      # Load upper half of return address (0x800c0150)
0x800c0198: ori  r15, r15, 0x150  # Load lower half of return address
0x800c019c: mtctr r15             # Move return address to count register
0x800c01a0: bctr                  # Branch to count register (jump back to game code)
```
**Notes:** 
This showcases exactly how to check the Slippi online scene (`0x80489D30`) and cross-reference the local player port pointer in the Small Data Area (`SDA`/`r13 - 0x49E4`). This is the key logic you will need to reuse for your Netplay safe Fox/Marth macro to ensure inputs are only spoofed locally and you are only reacting to the correct opponent.
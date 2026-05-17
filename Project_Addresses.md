# SSBM Memory Addresses for Fox/Marth Macro (1.02)

This document contains the necessary memory addresses, pointers, and offsets required to create an online-safe Gecko code macro that automatically triggers a Fox Shine (Down + B) when the opponent (Marth) initiates a Grab Action State.

## Pointers and Base Addresses
*These are the global starting points to find dynamic game data.*

| Description | Address | Notes |
| :--- | :--- | :--- |
| **Global Game State / Scene ID** | `0x80489D30` | Used to check if currently in an online match (Slippi). Usually compare bits to `0x208`. |
| **SDA Local Player Port Pointer** | `r13 - 0x49E4` | Points to a byte containing the local player's port ID (0-3). Essential for Netplay safety. |
| **Player 1 Static Block** | `0x80453080` | Base of Player 1's static data block. Each subsequent player (P2, P3, P4) is `0xE90` apart. |
| **P1 Character Entity Data Pointer**| `0x80453130` | Pointer to Player 1's dynamic Player Data structure. P2 is at `0x80453FC0`, P3 at `0x80454E50`, etc. (or just `0xE90` offset from P1's pointer). |

## Player Entity Data Offsets (Character Data)
*These offsets are relative to the pointer loaded from `0x80453130` (or the equivalent for other ports).*

| Description | Offset (Hex) | Size | Notes |
| :--- | :--- | :--- | :--- |
| **Player Port / Slot ID** | `0x000C` | Byte | Port ID (0 = P1, 1 = P2, etc.). Compare this with the local port. |
| **Character ID** | `0x0004` | Word | The internal character ID. (`0x00` = Mario, `0x01` = Fox, `0x12` = Marth, etc.) |
| **Action State ID** | `0x0010` | Word | The current action state ID of the character. |
| **Button Input Register** | `0x065C` | Word | Current frame's button inputs (used in UnclePunch macro). |

## Specific Values to Check/Spoof

### Internal Character IDs
| Character | ID (Hex) |
| :--- | :--- |
| **Fox** | `0x01` |
| **Marth** | `0x12` |

### Action State IDs (Grab/Catch)
| Action State | ID (Hex) | Notes |
| :--- | :--- | :--- |
| **Catch (Standing Grab)** | `0x00D4` | Standard standing grab startup/active frames. |
| **CatchDash (Dash Grab)** | `0x00D5` | Dash grab startup/active frames. |
| **CatchTurn (Pivot Grab)** | `0x00D6` | Turn grab. (Might need to check all 3). |

*(Note: Action State IDs for standard grabs are usually `0xD4`, `0xD5`, `0xD6`. A character-specific test may be required, but these are the universal grab initialization states).*

### Controller Input Bitmask (For `0x065C`)
| Input | Bit Value (Hex) |
| :--- | :--- |
| **B Button** | `0x0200` |
| **DPad Down** | `0x0004` | *(Note: Spoofing the analog stick `Y` value downwards might be required instead of just DPad Down depending on how the game processes Shine inputs. Stick Y is usually checked via analog values rather than digital bitmasks. However, if spoofing at the button level, B is `0x0200`)*.

# Build the FactoryAir Edge Node in Fusion — step by step

You will build a simple 3D model of the edge-node box: a plastic box with a lid,
a metal DIN rail inside, a power supply, a circuit board with three small modules,
three cable glands on the front, and the pressure tap that screws onto the
compressor. Every size comes from real parts. Take it slowly: about 2 hours.

**Golden rules (read once):**
- Made a mistake? Press **Ctrl + Z** (undo). Press it again to undo more.
- Stuck inside a tool? Press **Esc** to get out.
- Lost the model on screen? Press the little **house icon** near the top-right cube (Home view).
- Spin the view: hold **Shift** + hold the **middle mouse button (scroll wheel)** and move the mouse. Zoom: roll the scroll wheel.
- When I say **type 200**, click in the box, type the number, press **Enter**.
- All sizes are in **millimetres (mm)**.

---

## Part 0 — Get ready (10 minutes, only once)

1. Install **Fusion** (free for personal use) from Autodesk's website and sign in.
2. Open Fusion. You will see a grey grid. That is your empty workspace.
3. **Make "up" point up** (this makes every step below simpler):
   1. Click your **initials / profile picture** at the top-right corner.
   2. Click **Preferences**.
   3. On the left click **General**, then **Design**.
   4. Find **Default modeling orientation** and choose **Z up**.
   5. Click **Apply**, then **OK**.
4. Start a fresh design: top-left **File → New Design**.
5. Save it straight away: **File → Save** → name it **FactoryAir Edge Node** → **Save**.
   (Save again with **Ctrl + S** after every part.)

Look at the screen:
- **Left side** = the **Browser** (a list of everything you make).
- **Bottom** = the **Timeline** (a row of little icons, one per step — the judges want to see this).
- **Top** = the toolbar. Make sure the **SOLID** tab is selected.

---

## How to make a new part (you will repeat this for every part)

1. In the Browser (left), **right-click the very top item** (the design's name).
2. Click **New Component**.
3. In **Name**, type the part name I give you (for example `Box`) and click **OK**.
4. Next to the new name there is a small dot. **A filled dot means "I am working on this part now."** Good.

## How to move a part to its place

1. In the Browser, **click the part's name** so it turns blue.
2. Press the **M** key (Move/Copy).
3. In the window that opens, check **Move Type** is **Translate** (the arrows icon).
4. Type the numbers I give you into **X Distance**, **Y Distance**, **Z Distance**.
5. Click **OK**.

---

## Part 1 — The box (the main body)

Real part: IP65 plastic box, 200 × 150 × 75 mm.

1. Make a new part called **`Box`** (see "How to make a new part").
2. Top toolbar: **CREATE → Create Sketch**.
3. The screen shows three see-through squares (planes). **Click the flat one lying down like a table** (it is called XY).
4. The view turns to look from the top. In the toolbar click **CREATE → Rectangle → Center Rectangle**.
5. **Click the little dot in the middle of the grid** (the origin). This is the centre of the box.
6. Move the mouse a bit. Two boxes with numbers appear. Type **200**, press **Tab**, type **150**, press **Enter**.
7. Click **FINISH SKETCH** (the green tick at the top-right of the toolbar).
8. Press the **E** key (Extrude). Click inside the rectangle (it turns blue).
9. In **Distance**, type **75**. Click **OK**. You now have a solid block.
10. Make it hollow: **MODIFY → Shell**. **Click the top face** of the block. In **Inside Thickness** type **3**. Click **OK**. It is now an open box.
11. Round the corners: press **F** (Fillet). Click the **4 vertical outside corner edges** (spin the view to reach the back ones). In **Radius** type **8**. Click **OK**.
12. **Ctrl + S** to save.

✅ You should see an open-top box with rounded corners. **Send me a screenshot here.**

---

## Part 2 — The lid

1. New part: **`Lid`**.
2. **CREATE → Create Sketch** → click the flat (XY) plane.
3. **Center Rectangle** → click the origin dot → type **200**, **Tab**, **150**, **Enter**.
4. **FINISH SKETCH**.
5. Press **E** → click the rectangle → Distance **10** → **OK**.
6. Press **F** → click the 4 vertical corner edges of the lid → Radius **8** → **OK**.
7. The lid is inside the box now. Move it on top: click **Lid** in the Browser → **M** → X **0**, Y **0**, Z **75** → **OK**.
8. **Ctrl + S**.

Tip: to see inside the box later, click the little **eye icon** next to **Lid** in the Browser to hide it.

---

## Part 3 — The DIN rail (metal strip the parts clip onto)

Real part: 35 mm "top-hat" DIN rail, 7.5 mm tall. We cut it 180 mm long.

1. Hide the lid (eye icon). New part: **`DIN Rail`**.
2. **Create Sketch** → flat plane → **Center Rectangle** → origin → **180**, **Tab**, **35**, **Enter** → **FINISH SKETCH**.
3. **E** → click it → Distance **7.5** → **OK**.
4. Make it a channel: **MODIFY → Shell** → this time **click the BOTTOM face** (spin the view to see underneath) → Inside Thickness **1** → **OK**.
5. Move it into the box: click **DIN Rail** → **M** → X **0**, Y **25**, Z **3** → **OK**.
6. **Ctrl + S**.

---

## Part 4 — The 24 V power supply

Real part: Mean Well HDR-15-24 DIN-rail supply, 17.5 × 90 × 54.5 mm.

1. New part: **`PSU 24V`**.
2. **Create Sketch** → flat plane → **Center Rectangle** → origin → **17.5**, **Tab**, **90**, **Enter** → **FINISH SKETCH**.
3. **E** → Distance **54.5** → **OK**.
4. Move it onto the left end of the rail: **M** → X **-75**, Y **25**, Z **10.5** → **OK**.
5. **Ctrl + S**.

---

## Part 5 — The circuit board (holds the electronics)

Real part: 110 × 70 mm board, 1.6 mm thick, sitting 10 mm above the floor.

1. New part: **`Carrier Board`**.
2. **Create Sketch** → flat plane → **Center Rectangle** → origin → **110**, **Tab**, **70**, **Enter** → **FINISH SKETCH**.
3. **E** → Distance **1.6** → **OK**.
4. **M** → X **30**, Y **-30**, Z **13** → **OK**.
5. **Ctrl + S**.

---

## Part 6 — The four small modules on the board

Do the same 4 steps for each module below (new part → sketch rectangle → extrude → move):

| Part name | Rectangle (Tab between) | Extrude | Move X | Move Y | Move Z |
|---|---|---|---|---|---|
| `ESP32-S3` | 69, 25.5 | 10 | 10 | -40 | 14.6 |
| `ADS1115 ADC` | 28, 17 | 3 | 65 | -5 | 14.6 |
| `microSD` | 24, 42 | 3 | 70 | -40 | 14.6 |
| `Buck 5V` | 43, 21 | 14 | 5 | -12 | 14.6 |

Remember: for each one, **CREATE → Create Sketch → flat plane → Center Rectangle → origin → numbers → FINISH SKETCH → E → number → OK**, then click the part name → **M** → the three numbers → **OK**.

**Ctrl + S** when all four are done.

✅ Look inside: the power supply stands on the rail at the back-left; the board with four modules sits at the front-right. **Send me a screenshot.**

---

## Part 7 — Three cable glands on the front

Real part: PG7 cable gland, about 15 mm across. One each for the current clamp, the pressure transmitter and the machine-on contact.

1. New part: **`Gland 1`**.
2. **CREATE → Create Sketch** → this time **click the standing-up plane that faces you** (called XZ).
3. Toolbar: **CREATE → Circle → Center Diameter Circle** → click the origin dot → type **15** → **Enter** → **FINISH SKETCH**.
4. **E** → click the circle → Distance **20** → **OK**. (If it grows the wrong way it does not matter.)
5. **M** → X **-50**, Y **-75**, Z **30** → **OK**.
6. Make the other two as copies: click **Gland 1** → **M** → tick **Create Copy** → X **50**, Y **0**, Z **0** → **OK**. Do it once more on the new copy → X **50** → **OK**.
7. **Ctrl + S**.

---

## Part 8 — The pressure tap (outside the box)

This screws onto the compressor's gauge port: a tee, a ball valve with a handle, then the pressure transmitter. You draw every piece in the middle of the flat plane, and use **Start → Offset** in the Extrude window to lift it to the right height. That stacks the pieces neatly.

1. New part: **`Pressure Tap`**.
2. **Tee:** Create Sketch → flat plane → **Center Diameter Circle** → click the origin → **16** → Enter → **FINISH SKETCH** → **E** → click the circle → Distance **30** → **OK**.
3. **Ball valve:** Create Sketch → flat plane → Center Diameter Circle → origin → **22** → Enter → **FINISH SKETCH** → **E** → click the circle → in the Extrude window find **Start**, change it from *Profile Plane* to **Offset**, type **30** in the offset box → Distance **40** → **OK**. (It sits on top of the tee.)
4. **Transmitter:** Create Sketch → flat plane → Center Diameter Circle → origin → **27** → Enter → **FINISH SKETCH** → **E** → click the circle → Start **Offset** → **70** → Distance **80** → **OK**.
5. **Valve handle:** Create Sketch → flat plane → **Center Rectangle** → origin → **50**, Tab, **8** → Enter → **FINISH SKETCH** → **E** → click the rectangle → Start **Offset** → **50** → Distance **6** → **OK**. (A flat bar sticking out of the valve.)
6. Move the whole tap beside the box: click **Pressure Tap** in the Browser → **M** → X **170**, Y **0**, Z **0** → **OK**.
7. **Ctrl + S**.

Tip: in steps 3–5 the Browser may ask which body to click; always click the **newest sketch shape** (it highlights blue).

---

## Part 9 — Colours (makes the screenshot look professional)

1. Press **A** (Appearance).
2. In the search box type **ABS** → drag **"Plastic - Matte (Grey)"** or any light plastic onto the **Box** and **Lid**.
3. Search **steel** → drag onto **DIN Rail**.
4. Search **blue** → drag a blue plastic onto **PSU 24V**.
5. Search **green** → drag green onto **Carrier Board**; **black** onto the four modules and the glands.
6. Search **brass** → drag onto **Pressure Tap**.
7. **Close**. **Ctrl + S**.

---

## Part 10 — The pictures for the slide (this is what the judges check)

**Picture 1 — isometric view with the feature tree:**
1. Show the lid (eye icon on) or leave it hidden to show the inside — your choice (hidden looks better).
2. Click the **house icon** (Home view).
3. Make sure the **Browser** (left) is open with all parts listed and the **Timeline** (bottom) is visible.
4. Press **Windows + Shift + S** and drag over the whole Fusion window. Save it as `cad_isometric.png`.

**Picture 2 — exploded view:**
1. Top-left workspace menu (says **DESIGN**) → choose **ANIMATION**.
2. Toolbar: **Auto Explode: All Levels**. Click the whole model, click the green tick.
3. Press the **Play** button at the bottom, then pause near the end so parts are spread apart.
4. **Windows + Shift + S** → save as `cad_exploded.png`.
5. Go back: workspace menu → **DESIGN**.

**Picture 3 (optional) — a render:** workspace menu → **RENDER** → **In-canvas render** (teapot icon) → wait 1 minute → screenshot.

---

## Part 11 — Save the native files (the template asks for these)

1. **File → Export**.
2. **Type: Fusion Archive Files (*.f3d)** → location: `Desktop\FactoryAirTwin\cad` → **Export**.
3. **File → Export** again → **Type: STEP Files (*.step)** → same folder → **Export**.
4. Put your two screenshots in the same `cad` folder.

Then tell me — I will place the pictures on slide 8 of the deck and add the files to GitHub.

---

## If something goes wrong

| Problem | Fix |
|---|---|
| A part appeared in the wrong place | Click its name → **M** → move it by the difference, or **Ctrl + Z** and redo the Move |
| I extruded the wrong thing | **Ctrl + Z** |
| Everything is grey and I can't click a part | Click the **dot next to the top item** in the Browser to go back to the whole design |
| I can't find a tool | Press **S** and type its name (e.g. "shell") |
| The rectangle won't start at the centre | Zoom in on the origin dot and click exactly on it (a small square highlight appears) |
| I'm completely lost | Send me a screenshot — I'll tell you exactly what to click |

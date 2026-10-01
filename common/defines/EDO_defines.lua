NDefines.NGame.START_DATE = "1936.1.1.12"
NDefines.NGame.END_DATE = "1970.1.1.1"

NDefines.NFrontend.CAMERA_MIN_HEIGHT = 50.0
NDefines.NFrontend.CAMERA_MAX_HEIGHT = 3800.0
NDefines.NGraphics.PROVINCE_NAME_DRAW_DISTANCE = 1000.0 			-- Remove province names beyond this distance

NDefines.NGame.MAP_SCALE_PIXEL_TO_KM = 1.74
NDefines.NGame.SAVE_VERSION = 7
NDefines.NGame.HANDS_OFF_START_TAG = "MAP"

NDefines.NMilitary.LAND_SPEED_MODIFIER = 0.18
NDefines.NMilitary.BASE_CAPTURE_EQUIPMENT_RATIO = 0.01			-- after a successful land combat, ratio of the equipments that are being captured/salvaged from enemy's lost equipment
NDefines.NMilitary.MIN_SUPPLY_CONSUMPTION = 0.025

NDefines.NProduction.EQUIPMENT_BASE_LEND_LEASE_WEIGHT = 0.5
NDefines.NProduction.LEND_LEASE_DELIVERY_TOTAL_DAYS = 10 -- Nr of days between lend lease deliveries

NDefines.NSupply.CAPITAL_SUPPLY_BASE = 1.6 -- base supply for capital
NDefines.NSupply.CAPITAL_INITIAL_SUPPLY_FLOW = 3.0 -- starting supply from capital
NDefines.NSupply.CAPITAL_STARTING_PENALTY_PER_PROVINCE = 0.3 -- starting penalty that will be added as supply moves away from its origin (modified by stuff like terrain)
NDefines.NSupply.CAPITAL_ADDED_PENALTY_PER_PROVINCE = 0.4 -- added penalty as we move away from origin
NDefines.NSupply.NODE_INITIAL_SUPPLY_FLOW = 2.0
NDefines.NSupply.NODE_STARTING_PENALTY_PER_PROVINCE = 0.3
NDefines.NSupply.NODE_ADDED_PENALTY_PER_PROVINCE = 0.4
NDefines.NSupply.INFRA_TO_SUPPLY = 0.5							-- each level of infra gives this many supply
NDefines.NSupply.VP_TO_SUPPLY_BONUS_CONVERSION = 0.02			-- Bonus to supply local supplies from Victory Points, multiplied by this aspect and rounded to closest integer
NDefines.NSupply.VP_TO_SUPPLY_BASE = 0							-- Bonus to supply from a VP, no matter the level
NDefines.NSupply.NAVAL_BASE_INITIAL_SUPPLY_FLOW = 0.8
NDefines.NSupply.NAVAL_BASE_STARTING_PENALTY_PER_PROVINCE = 0.6
NDefines.NSupply.NAVAL_BASE_ADDED_PENALTY_PER_PROVINCE = 0.6
NDefines.NSupply.FLOATING_HARBOR_INITIAL_SUPPLY_FLOW = 0.8
NDefines.NSupply.NUM_RAILWAYS_TRAIN_FACTOR = 0.03  --Distance should matter much more now
NDefines.NSupply.DEFAULT_STARTING_TRAIN_RATIO = 2.0
NDefines.NSupply.TRUCK_ATTRITION = 0.01  --More supply truck attrition
NDefines.NSupply.AVAILABLE_MANPOWER_STATE_SUPPLY = 0.1
NDefines.NSupply.NON_CORE_MANPOWER_STATE_SUPPLY = 0.05

NDefines.NBuildings.RADAR_RANGE_BASE = 100				-- Radar range base, first level radar will be this + min, best radar will be this + max
NDefines.NBuildings.RADAR_RANGE_MIN = 100				-- Radar range (from state center to province center) in measure of map pixels. Exluding techs.
NDefines.NBuildings.RADAR_RANGE_MAX = 1000				-- Range is interpolated between building levels 1-15.

NDefines.NAir.AIR_REGION_SUPERIORITY_PIXEL_SCALE = 0.02

NDefines.NCountry.MAJOR_IC_RATIO = 10                             -- difference in total factories needed to be considered major with respect to other nation
NDefines.NCountry.MAJOR_MIN_FACTORIES = 70						-- need at least these many factories to become a major

NDefines.NAI.WANTED_UNITS_INDUSTRY_FACTOR = 1.50                        -- How many units a country wants is partially based on how much military industry that is available
NDefines.NAI.WANTED_UNITS_THREAT_BASE = 0.7                             -- If no threat, multiply min wanted units by this
NDefines.NAI.WANTED_UNITS_THREAT_MAX = 6.0                             -- Normalized threat is clamped to this
NDefines.NAI.WANTED_UNITS_WAR_THREAT_FACTOR = 1.5                      -- Factor threat with this if country is at war. this value is overriden by the value in ideology database if that value exceedes this.
NDefines.NAI.WANTED_UNITS_DANGEROUS_NEIGHBOR_FACTOR = 1.15              -- Factor if has dangerous neighbor
NDefines.NAI.WANTED_UNITS_MANPOWER_DIVISOR = 14000                      -- Normalizing divisor for AI manpower. (for each x max available manpower, they want one division)
NDefines.NAI.WANTED_UNITS_WEIGHT_FRONTS_WANT = 0.40                      -- Weight of front needs when computing final nr wanted units
NDefines.NAI.WANTED_UNITS_WEIGHT_FACTORIES = 0.45                        -- Weight of military factories when computing final nr wanted units
NDefines.NAI.WANTED_UNITS_WEIGHT_MANPOWER = 0.3                         -- Weight of manpower availability when computing final nr wanted units
NDefines.NAI.WANTED_UNITS_MIN_DEFENCE_FACTOR = 0.4						-- Factor on units required for min defence
-- End of calculating wanted nr of divisions
NDefines.NAI.WANTED_UNITS_MAX_WANTED_CAP = 800	-- Maximum wanted divisions for a country. This can be exceeded by certain hardcoded multipliers, but not by base calculation logic.

NDefines.NNavy.NAVAL_SPEED_MODIFIER = 0.3
NDefines.NNavy.NAVAL_TRANSFER_BASE_SPEED = 12
NDefines.NNavy.NAVAL_RANGE_TO_INGAME_DISTANCE = 0.24							-- Scale the ship stats "naval_range" to the ingame distance

NDefines.NGraphics.MINIMUM_PROVINCE_SIZE_IN_PIXELS = 4

NDefines.NGraphics.COUNTRY_FLAG_LARGE_STRIPE_MAX_HEIGHT = 24000        -- VANILLA: 8192
NDefines.NGraphics.COUNTRY_FLAG_SMALL_TEX_MAX_SIZE = 512            -- VANILLA: 64
NDefines.NGraphics.COUNTRY_FLAG_STRIPE_TEX_MAX_HEIGHT = 8196        -- VANILLA: 2048
NDefines.NGraphics.COUNTRY_FLAG_TEX_MAX_SIZE = 2048                    -- VANILLA: 256
NDefines.NGraphics.VICTORY_POINT_MAP_ICON_TEXT_CUTOFF = {225, 375, 750}  -- At what camera distance the VP name text disappears.
NDefines.NGraphics.VICTORY_POINTS_DISTANCE_CUTOFF = {375,750,1500} -- At what distance VPs are hidden
NDefines.NGraphics.CAPITAL_ICON_CUTOFF = 1650	-- At what camera distance capital icons disappears
NDefines.NGraphics.UNITS_DISTANCE_CUTOFF = 180
NDefines.NGraphics.SHIPS_DISTANCE_CUTOFF = 360
NDefines.NGraphics.MAP_ICONS_GROUP_CAM_DISTANCE = 145.0 -- camera distance at which the icons begin to group up
NDefines.NGraphics.MAP_ICONS_STATE_GROUP_CAM_DISTANCE = 270.0 -- Camera distance at which the icons begin to group up on state level
NDefines.NGraphics.MAP_ICONS_STRATEGIC_GROUP_CAM_DISTANCE = 525 -- second camera distance at which the icons begin to group up

NDefines.NMapMode.MAP_MODE_MANPOWER_RANGE_MAX = 5000000
NDefines.NMapMode.MAP_MODE_MANPOWER_RANGE_COLOR_FROM = { 0.2, 0.2, 0.7, 0.5 }
NDefines.NMapMode.MAP_MODE_MANPOWER_RANGE_COLOR_TO = { 1, 0.125, 0.0, 0.75 }

NDefines.NDiplomacy.TENSION_NO_CB_WAR = 6 -- VANILLA: 10
NDefines.NDiplomacy.TENSION_CB_WAR = 4 -- VANILLA: 7
NDefines.NDiplomacy.TENSION_DECAY_DAILY = 0.01 -- VANILLA: 0.005
NDefines.NDiplomacy.TENSION_SIZE_FACTOR = 0.9 --VANILLA: 1
NDefines.NDiplomacy.PEACE_SCORE_SCALE_FACTOR = 2.0 --Vanilla 1.35
NDefines.NDiplomacy.TENSION_ANNEX_NO_CLAIM = 0.5	-- VANILLA: 2
NDefines.NDiplomacy.TENSION_ANNEX_CLAIM = 0.1	-- VANILLA: 0.5

NDefines.NOperatives.INTEL_NETWORK_GAIN_RATE_ON_OUT_OF_RANGE = -0.5				-- Amount of network strength lost in a state that has the right controller but is out of range of any operative
NDefines.NOperatives.INTEL_NETWORK_GAIN_DECAY_PER_STEP_FACTOR = 0.8				-- Factor multiplied to the gain of the previous node in the netowrk initially contributed by the agent. In other words, before adjacency, the strength gain in a state would be GainFromOperative * ( INTEL_NETWORK_GAIN_DECAY_PER_STEP_FACTOR ^ NodeDepth ) where NodeDepth is the distance between the state and the operative's location.
NDefines.NOperatives.INTEL_NETWORK_STRENGTH_DECAY_WHEN_ABOVE_TARGET = -1.5			-- The amount of strength removed each tick from a state that has more strength than the target
NDefines.NOperatives.INTEL_NETWORK_OPERATIVE_GAIN_STACKING_FACTOR = 0.9			-- When multiple operative are present in the same location, this factor is applied for each operative with a lower gain than the max. So if operatives have the gain [ 3, 1, 2 ] in the same location, it is sorted to [ 1, 2, 3 ] then converted to [ 1*D^2, 2*D^1, 3 ], with D being this define, so if D=0.5 we have [ 0.25, 1, 3 ] and the final gain from operative at this location will be 4.25. Putting this define to 0 is equivalent to considering the maximum value only.

NDefines.NMilitary.CORPS_COMMANDER_DIVISIONS_CAP = 48	-- How many divisions a corps commander is limited to. 0 = inf, < 0 = blocked
NDefines.NMilitary.FIELD_MARSHAL_DIVISIONS_CAP = 72	-- How many divisions a field marshall is limited to. 0 = inf, < 0 = blocked

NDefines.NResistance.SUPPRESSION_NEEDED_BY_RESISTANCE_POINT = 0.6 --Reduced from 0.75

-- AI behavior tuning: aligned with ME supply rework (organic supply gutted,
-- infra/rail/hubs carry the load) and doubled-division frontage design.
-- See _ctx/ai-enhancement-research.md §7 for evidence.

-- Production discipline: don't keep queueing divisions while field units starve
NDefines.NAI.MIN_FIELD_STRENGTH_TO_BUILD_UNITS = 0.75		-- VANILLA: 0.7. 0.85 hard-stopped GER at 335 divs pre-Barbarossa (obs 2026-10-01); halved battalion costs make 0.75 sufficient discipline
NDefines.NAI.PRODUCTION_EQUIPMENT_SURPLUS_FACTOR = 1.2	-- VANILLA: 0.8
NDefines.NAI.UPGRADE_DIVISION_RELUCTANCE = 3				-- VANILLA: 7. Upgrade existing divisions more often
NDefines.NAI.UPGRADE_PERCENTAGE_OF_FORCES = 0.35			-- VANILLA: 0.20
NDefines.NAI.DESIRE_USE_XP_TO_UPDATE_LAND_TEMPLATE = 4.0	-- VANILLA: 2.0

-- Front allocation: fewer ally-front pileups, lower per-tile density
NDefines.NAI.FRONT_EVAL_PERCENT_TO_ASSIST_ALLY_FRONT = 1.0	-- VANILLA: 0.5
NDefines.NAI.FRONTLINE_EXPANSION_FACTOR = 0.4				-- VANILLA: 0.6
NDefines.NAI.FRONT_UNITS_CAP_FACTOR = 10.0					-- VANILLA: 15.0
NDefines.NAI.AIFC_UNIT_RATIO_BASE = 0.25					-- VANILLA: 0.15. Bigger spearhead reserve

-- AIFC pathing: breakthroughs should follow railways and supply hubs
NDefines.NAI.AIFC_PATH_COST_RAILWAY_CONNECTION = 0.35		-- VANILLA: 0.75
NDefines.NAI.AIFC_PATH_COST_HAS_SUPPLY_HUB = 0.3			-- VANILLA: 0.5
NDefines.NAI.AIFC_TARGET_SUPPLY_HUB_BASE_SCORE = 40.0		-- VANILLA: 20.0
NDefines.NAI.AIFC_TARGET_CAPITAL_SCORE_EXTRA = 25.0			-- VANILLA: 5.0. Capital hubs were drowning under supply-hub score 40; capitals should outrank ordinary hubs

-- AI supply awareness: vanilla values assume the old organic-supply economy
NDefines.NAI.CONSTRUCTION_PRIO_SUPPLY_BUILDING = 1.5		-- VANILLA: 1.10. Boosted for ME supply economy but kept below factories after x2 core factor (3.0 vs CIV 1.6/MIL 1.4 is enough)
NDefines.NAI.CONSTRUCTION_PRIO_INFRASTRUCTURE = 1.0		-- VANILLA: 0.20. INFRA_TO_SUPPLY=0.5 makes infra the supply backbone — keep at factory tier
NDefines.NAI.CONSTRUCTION_PRIO_RAILWAY = 6.0				-- VANILLA: 4.00
NDefines.NAI.LAND_DEFENSE_SUPPLY_HUB_IMPORTANCE = 30.0	-- VANILLA: 4.0
NDefines.NAI.STR_BOMB_SUPPLY_HUB_IMPORTANCE = 4.0			-- VANILLA: 1.0
NDefines.NAI.AI_FRONT_DIVISIONS_PER_SUPPLY_POINT = 0.5	-- VANILLA: 1.0. Supply per point is scarcer now
NDefines.NAI.MAX_SUPPLY_DIVISOR = 2.0						-- VANILLA: 1.75. Higher = AI assumes less supply per unit, builds/conserves more carefully
NDefines.NAI.AI_THEATRE_SUPPLY_CRISIS_LIMIT = 0.15		-- VANILLA: 0.1. Bail out of starved areas earlier

NDefines.NAITheatre.AI_THEATRE_DISTRIBUTION_MAX_PERCENT_UNMET_DEMAND_PER_FRONT = 0.35	-- VANILLA: 0.5. Less unit ping-pong between fronts
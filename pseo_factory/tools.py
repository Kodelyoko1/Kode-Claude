"""
pSEO Factory — programmatic SEO landing-page generator.

Generates city-by-city "We Buy Houses" landing pages for wholesale real estate.
Each page targets a long-tail keyword like "we buy houses [city] [state]" and
includes locally-tuned copy, FAQs, and an HTML file ready to drop into the
website/ directory or upload to any static host.

Config via data/pseo_config.json:
  {
    "markets": [{"city": "Portland", "state": "ME", "county": "Cumberland"}, ...],
    "business_name": "Wholesale Omniverse",
    "phone": "207-385-4041",
    "email": "WholesaleOmniverse@gmail.com"
  }

Outputs:
  data/pseo_pages/{slug}.html    — standalone landing page
  data/pseo_pages/{slug}.md      — markdown version (for blog/CMS)
  data/pseo_index.json           — registry of all generated pages

Entry point: run_full_cycle()
"""
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from autonomous import storage, mailer, metrics, billing

AGENT_KEY  = "pseo_factory"
PAGES_DIR  = Path(__file__).parent.parent / "data" / "pseo_pages"
PAGES_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_CONFIG = {
    "markets": [
        # Alabama
        {"city": "Birmingham",      "state": "AL", "county": "Jefferson"},
        {"city": "Montgomery",      "state": "AL", "county": "Montgomery"},
        {"city": "Huntsville",      "state": "AL", "county": "Madison"},
        {"city": "Mobile",          "state": "AL", "county": "Mobile"},
        {"city": "Tuscaloosa",      "state": "AL", "county": "Tuscaloosa"},
        {"city": "Dothan",          "state": "AL", "county": "Houston"},
        {"city": "Decatur",         "state": "AL", "county": "Morgan"},
        # Alaska
        {"city": "Anchorage",       "state": "AK", "county": "Anchorage"},
        {"city": "Fairbanks",       "state": "AK", "county": "Fairbanks North Star"},
        {"city": "Juneau",          "state": "AK", "county": "Juneau"},
        {"city": "Wasilla",         "state": "AK", "county": "Matanuska-Susitna"},
        {"city": "Sitka",           "state": "AK", "county": "Sitka"},
        # Arizona
        {"city": "Phoenix",         "state": "AZ", "county": "Maricopa"},
        {"city": "Tucson",          "state": "AZ", "county": "Pima"},
        {"city": "Mesa",            "state": "AZ", "county": "Maricopa"},
        {"city": "Chandler",        "state": "AZ", "county": "Maricopa"},
        {"city": "Scottsdale",      "state": "AZ", "county": "Maricopa"},
        {"city": "Glendale",        "state": "AZ", "county": "Maricopa"},
        {"city": "Gilbert",         "state": "AZ", "county": "Maricopa"},
        {"city": "Tempe",           "state": "AZ", "county": "Maricopa"},
        {"city": "Peoria",          "state": "AZ", "county": "Maricopa"},
        # Arkansas
        {"city": "Little Rock",     "state": "AR", "county": "Pulaski"},
        {"city": "Fort Smith",      "state": "AR", "county": "Sebastian"},
        {"city": "Fayetteville",    "state": "AR", "county": "Washington"},
        {"city": "Springdale",      "state": "AR", "county": "Washington"},
        {"city": "Jonesboro",       "state": "AR", "county": "Craighead"},
        {"city": "North Little Rock","state": "AR", "county": "Pulaski"},
        # California
        {"city": "Los Angeles",     "state": "CA", "county": "Los Angeles"},
        {"city": "San Diego",       "state": "CA", "county": "San Diego"},
        {"city": "San Jose",        "state": "CA", "county": "Santa Clara"},
        {"city": "San Francisco",   "state": "CA", "county": "San Francisco"},
        {"city": "Fresno",          "state": "CA", "county": "Fresno"},
        {"city": "Sacramento",      "state": "CA", "county": "Sacramento"},
        {"city": "Riverside",       "state": "CA", "county": "Riverside"},
        {"city": "Stockton",        "state": "CA", "county": "San Joaquin"},
        {"city": "Oakland",         "state": "CA", "county": "Alameda"},
        {"city": "Bakersfield",     "state": "CA", "county": "Kern"},
        # Colorado
        {"city": "Denver",          "state": "CO", "county": "Denver"},
        {"city": "Colorado Springs","state": "CO", "county": "El Paso"},
        {"city": "Aurora",          "state": "CO", "county": "Arapahoe"},
        {"city": "Fort Collins",    "state": "CO", "county": "Larimer"},
        {"city": "Lakewood",        "state": "CO", "county": "Jefferson"},
        {"city": "Pueblo",          "state": "CO", "county": "Pueblo"},
        {"city": "Thornton",        "state": "CO", "county": "Adams"},
        # Connecticut
        {"city": "Bridgeport",      "state": "CT", "county": "Fairfield"},
        {"city": "New Haven",       "state": "CT", "county": "New Haven"},
        {"city": "Hartford",        "state": "CT", "county": "Hartford"},
        {"city": "Stamford",        "state": "CT", "county": "Fairfield"},
        {"city": "Waterbury",       "state": "CT", "county": "New Haven"},
        {"city": "Norwalk",         "state": "CT", "county": "Fairfield"},
        # Delaware
        {"city": "Wilmington",      "state": "DE", "county": "New Castle"},
        {"city": "Dover",           "state": "DE", "county": "Kent"},
        {"city": "Newark",          "state": "DE", "county": "New Castle"},
        {"city": "Middletown",      "state": "DE", "county": "New Castle"},
        # Florida
        {"city": "Jacksonville",    "state": "FL", "county": "Duval"},
        {"city": "Miami",           "state": "FL", "county": "Miami-Dade"},
        {"city": "Tampa",           "state": "FL", "county": "Hillsborough"},
        {"city": "Orlando",         "state": "FL", "county": "Orange"},
        {"city": "St. Petersburg",  "state": "FL", "county": "Pinellas"},
        {"city": "Hialeah",         "state": "FL", "county": "Miami-Dade"},
        {"city": "Tallahassee",     "state": "FL", "county": "Leon"},
        {"city": "Fort Lauderdale", "state": "FL", "county": "Broward"},
        {"city": "Daytona Beach",   "state": "FL", "county": "Volusia"},
        {"city": "Clearwater",      "state": "FL", "county": "Pinellas"},
        # Georgia
        {"city": "Atlanta",         "state": "GA", "county": "Fulton"},
        {"city": "Augusta",         "state": "GA", "county": "Richmond"},
        {"city": "Columbus",        "state": "GA", "county": "Muscogee"},
        {"city": "Macon",           "state": "GA", "county": "Bibb"},
        {"city": "Savannah",        "state": "GA", "county": "Chatham"},
        {"city": "Athens",          "state": "GA", "county": "Clarke"},
        {"city": "Sandy Springs",   "state": "GA", "county": "Fulton"},
        {"city": "Roswell",         "state": "GA", "county": "Fulton"},
        # Hawaii
        {"city": "Honolulu",        "state": "HI", "county": "Honolulu"},
        {"city": "Pearl City",      "state": "HI", "county": "Honolulu"},
        {"city": "Hilo",            "state": "HI", "county": "Hawaii"},
        {"city": "Kailua",          "state": "HI", "county": "Honolulu"},
        {"city": "Waipahu",         "state": "HI", "county": "Honolulu"},
        # Idaho
        {"city": "Boise",           "state": "ID", "county": "Ada"},
        {"city": "Nampa",           "state": "ID", "county": "Canyon"},
        {"city": "Meridian",        "state": "ID", "county": "Ada"},
        {"city": "Idaho Falls",     "state": "ID", "county": "Bonneville"},
        {"city": "Pocatello",       "state": "ID", "county": "Bannock"},
        {"city": "Coeur d'Alene",   "state": "ID", "county": "Kootenai"},
        # Illinois
        {"city": "Chicago",         "state": "IL", "county": "Cook"},
        {"city": "Aurora",          "state": "IL", "county": "Kane"},
        {"city": "Joliet",          "state": "IL", "county": "Will"},
        {"city": "Rockford",        "state": "IL", "county": "Winnebago"},
        {"city": "Springfield",     "state": "IL", "county": "Sangamon"},
        {"city": "Naperville",      "state": "IL", "county": "DuPage"},
        {"city": "Peoria",          "state": "IL", "county": "Peoria"},
        {"city": "Elgin",           "state": "IL", "county": "Kane"},
        # Indiana
        {"city": "Indianapolis",    "state": "IN", "county": "Marion"},
        {"city": "Fort Wayne",      "state": "IN", "county": "Allen"},
        {"city": "Evansville",      "state": "IN", "county": "Vanderburgh"},
        {"city": "South Bend",      "state": "IN", "county": "St. Joseph"},
        {"city": "Carmel",          "state": "IN", "county": "Hamilton"},
        {"city": "Muncie",          "state": "IN", "county": "Delaware"},
        {"city": "Terre Haute",     "state": "IN", "county": "Vigo"},
        # Iowa
        {"city": "Des Moines",      "state": "IA", "county": "Polk"},
        {"city": "Cedar Rapids",    "state": "IA", "county": "Linn"},
        {"city": "Davenport",       "state": "IA", "county": "Scott"},
        {"city": "Sioux City",      "state": "IA", "county": "Woodbury"},
        {"city": "Iowa City",       "state": "IA", "county": "Johnson"},
        {"city": "Waterloo",        "state": "IA", "county": "Black Hawk"},
        # Kansas
        {"city": "Wichita",         "state": "KS", "county": "Sedgwick"},
        {"city": "Overland Park",   "state": "KS", "county": "Johnson"},
        {"city": "Kansas City",     "state": "KS", "county": "Wyandotte"},
        {"city": "Olathe",          "state": "KS", "county": "Johnson"},
        {"city": "Topeka",          "state": "KS", "county": "Shawnee"},
        {"city": "Lawrence",        "state": "KS", "county": "Douglas"},
        # Kentucky
        {"city": "Louisville",      "state": "KY", "county": "Jefferson"},
        {"city": "Lexington",       "state": "KY", "county": "Fayette"},
        {"city": "Bowling Green",   "state": "KY", "county": "Warren"},
        {"city": "Owensboro",       "state": "KY", "county": "Daviess"},
        {"city": "Covington",       "state": "KY", "county": "Kenton"},
        {"city": "Richmond",        "state": "KY", "county": "Madison"},
        # Louisiana
        {"city": "New Orleans",     "state": "LA", "county": "Orleans"},
        {"city": "Baton Rouge",     "state": "LA", "county": "East Baton Rouge"},
        {"city": "Shreveport",      "state": "LA", "county": "Caddo"},
        {"city": "Metairie",        "state": "LA", "county": "Jefferson"},
        {"city": "Lafayette",       "state": "LA", "county": "Lafayette"},
        {"city": "Lake Charles",    "state": "LA", "county": "Calcasieu"},
        # Maine
        {"city": "Portland",        "state": "ME", "county": "Cumberland"},
        {"city": "Bangor",          "state": "ME", "county": "Penobscot"},
        {"city": "Lewiston",        "state": "ME", "county": "Androscoggin"},
        {"city": "Auburn",          "state": "ME", "county": "Androscoggin"},
        {"city": "Augusta",         "state": "ME", "county": "Kennebec"},
        {"city": "Biddeford",       "state": "ME", "county": "York"},
        {"city": "South Portland",  "state": "ME", "county": "Cumberland"},
        {"city": "Sanford",         "state": "ME", "county": "York"},
        {"city": "Brunswick",       "state": "ME", "county": "Cumberland"},
        # Maryland
        {"city": "Baltimore",       "state": "MD", "county": "Baltimore City"},
        {"city": "Frederick",       "state": "MD", "county": "Frederick"},
        {"city": "Rockville",       "state": "MD", "county": "Montgomery"},
        {"city": "Gaithersburg",    "state": "MD", "county": "Montgomery"},
        {"city": "Bowie",           "state": "MD", "county": "Prince George's"},
        {"city": "Hagerstown",      "state": "MD", "county": "Washington"},
        {"city": "Annapolis",       "state": "MD", "county": "Anne Arundel"},
        # Massachusetts
        {"city": "Boston",          "state": "MA", "county": "Suffolk"},
        {"city": "Worcester",       "state": "MA", "county": "Worcester"},
        {"city": "Springfield",     "state": "MA", "county": "Hampden"},
        {"city": "Lowell",          "state": "MA", "county": "Middlesex"},
        {"city": "Cambridge",       "state": "MA", "county": "Middlesex"},
        {"city": "Brockton",        "state": "MA", "county": "Plymouth"},
        {"city": "New Bedford",     "state": "MA", "county": "Bristol"},
        # Michigan
        {"city": "Detroit",         "state": "MI", "county": "Wayne"},
        {"city": "Grand Rapids",    "state": "MI", "county": "Kent"},
        {"city": "Warren",          "state": "MI", "county": "Macomb"},
        {"city": "Sterling Heights","state": "MI", "county": "Macomb"},
        {"city": "Ann Arbor",       "state": "MI", "county": "Washtenaw"},
        {"city": "Lansing",         "state": "MI", "county": "Ingham"},
        {"city": "Flint",           "state": "MI", "county": "Genesee"},
        {"city": "Dearborn",        "state": "MI", "county": "Wayne"},
        # Minnesota
        {"city": "Minneapolis",     "state": "MN", "county": "Hennepin"},
        {"city": "Saint Paul",      "state": "MN", "county": "Ramsey"},
        {"city": "Rochester",       "state": "MN", "county": "Olmsted"},
        {"city": "Duluth",          "state": "MN", "county": "St. Louis"},
        {"city": "Bloomington",     "state": "MN", "county": "Hennepin"},
        {"city": "Plymouth",        "state": "MN", "county": "Hennepin"},
        {"city": "Brooklyn Park",   "state": "MN", "county": "Hennepin"},
        # Mississippi
        {"city": "Jackson",         "state": "MS", "county": "Hinds"},
        {"city": "Gulfport",        "state": "MS", "county": "Harrison"},
        {"city": "Southaven",       "state": "MS", "county": "DeSoto"},
        {"city": "Hattiesburg",     "state": "MS", "county": "Forrest"},
        {"city": "Biloxi",          "state": "MS", "county": "Harrison"},
        {"city": "Meridian",        "state": "MS", "county": "Lauderdale"},
        # Missouri
        {"city": "Kansas City",     "state": "MO", "county": "Jackson"},
        {"city": "St. Louis",       "state": "MO", "county": "St. Louis City"},
        {"city": "Springfield",     "state": "MO", "county": "Greene"},
        {"city": "Columbia",        "state": "MO", "county": "Boone"},
        {"city": "Independence",    "state": "MO", "county": "Jackson"},
        {"city": "St. Joseph",      "state": "MO", "county": "Buchanan"},
        {"city": "Lee's Summit",    "state": "MO", "county": "Jackson"},
        # Montana
        {"city": "Billings",        "state": "MT", "county": "Yellowstone"},
        {"city": "Missoula",        "state": "MT", "county": "Missoula"},
        {"city": "Great Falls",     "state": "MT", "county": "Cascade"},
        {"city": "Bozeman",         "state": "MT", "county": "Gallatin"},
        {"city": "Helena",          "state": "MT", "county": "Lewis and Clark"},
        # Nebraska
        {"city": "Omaha",           "state": "NE", "county": "Douglas"},
        {"city": "Lincoln",         "state": "NE", "county": "Lancaster"},
        {"city": "Bellevue",        "state": "NE", "county": "Sarpy"},
        {"city": "Grand Island",    "state": "NE", "county": "Hall"},
        {"city": "Kearney",         "state": "NE", "county": "Buffalo"},
        # Nevada
        {"city": "Las Vegas",       "state": "NV", "county": "Clark"},
        {"city": "Henderson",       "state": "NV", "county": "Clark"},
        {"city": "Reno",            "state": "NV", "county": "Washoe"},
        {"city": "North Las Vegas", "state": "NV", "county": "Clark"},
        {"city": "Sparks",          "state": "NV", "county": "Washoe"},
        {"city": "Carson City",     "state": "NV", "county": "Carson City"},
        # New Hampshire
        {"city": "Manchester",      "state": "NH", "county": "Hillsborough"},
        {"city": "Nashua",          "state": "NH", "county": "Hillsborough"},
        {"city": "Concord",         "state": "NH", "county": "Merrimack"},
        {"city": "Dover",           "state": "NH", "county": "Strafford"},
        {"city": "Rochester",       "state": "NH", "county": "Strafford"},
        # New Jersey
        {"city": "Newark",          "state": "NJ", "county": "Essex"},
        {"city": "Jersey City",     "state": "NJ", "county": "Hudson"},
        {"city": "Paterson",        "state": "NJ", "county": "Passaic"},
        {"city": "Elizabeth",       "state": "NJ", "county": "Union"},
        {"city": "Trenton",         "state": "NJ", "county": "Mercer"},
        {"city": "Camden",          "state": "NJ", "county": "Camden"},
        {"city": "Toms River",      "state": "NJ", "county": "Ocean"},
        # New Mexico
        {"city": "Albuquerque",     "state": "NM", "county": "Bernalillo"},
        {"city": "Las Cruces",      "state": "NM", "county": "Dona Ana"},
        {"city": "Rio Rancho",      "state": "NM", "county": "Sandoval"},
        {"city": "Santa Fe",        "state": "NM", "county": "Santa Fe"},
        {"city": "Roswell",         "state": "NM", "county": "Chaves"},
        # New York
        {"city": "New York City",   "state": "NY", "county": "New York"},
        {"city": "Buffalo",         "state": "NY", "county": "Erie"},
        {"city": "Rochester",       "state": "NY", "county": "Monroe"},
        {"city": "Yonkers",         "state": "NY", "county": "Westchester"},
        {"city": "Syracuse",        "state": "NY", "county": "Onondaga"},
        {"city": "Albany",          "state": "NY", "county": "Albany"},
        {"city": "New Rochelle",    "state": "NY", "county": "Westchester"},
        {"city": "Utica",           "state": "NY", "county": "Oneida"},
        # North Carolina
        {"city": "Charlotte",       "state": "NC", "county": "Mecklenburg"},
        {"city": "Raleigh",         "state": "NC", "county": "Wake"},
        {"city": "Greensboro",      "state": "NC", "county": "Guilford"},
        {"city": "Durham",          "state": "NC", "county": "Durham"},
        {"city": "Winston-Salem",   "state": "NC", "county": "Forsyth"},
        {"city": "Fayetteville",    "state": "NC", "county": "Cumberland"},
        {"city": "Cary",            "state": "NC", "county": "Wake"},
        {"city": "Wilmington",      "state": "NC", "county": "New Hanover"},
        # North Dakota
        {"city": "Fargo",           "state": "ND", "county": "Cass"},
        {"city": "Bismarck",        "state": "ND", "county": "Burleigh"},
        {"city": "Grand Forks",     "state": "ND", "county": "Grand Forks"},
        {"city": "Minot",           "state": "ND", "county": "Ward"},
        {"city": "Mandan",          "state": "ND", "county": "Morton"},
        # Ohio
        {"city": "Columbus",        "state": "OH", "county": "Franklin"},
        {"city": "Cleveland",       "state": "OH", "county": "Cuyahoga"},
        {"city": "Cincinnati",      "state": "OH", "county": "Hamilton"},
        {"city": "Toledo",          "state": "OH", "county": "Lucas"},
        {"city": "Akron",           "state": "OH", "county": "Summit"},
        {"city": "Dayton",          "state": "OH", "county": "Montgomery"},
        {"city": "Youngstown",      "state": "OH", "county": "Mahoning"},
        {"city": "Canton",          "state": "OH", "county": "Stark"},
        # Oklahoma
        {"city": "Oklahoma City",   "state": "OK", "county": "Oklahoma"},
        {"city": "Tulsa",           "state": "OK", "county": "Tulsa"},
        {"city": "Norman",          "state": "OK", "county": "Cleveland"},
        {"city": "Broken Arrow",    "state": "OK", "county": "Wagoner"},
        {"city": "Edmond",          "state": "OK", "county": "Oklahoma"},
        {"city": "Lawton",          "state": "OK", "county": "Comanche"},
        # Oregon
        {"city": "Portland",        "state": "OR", "county": "Multnomah"},
        {"city": "Salem",           "state": "OR", "county": "Marion"},
        {"city": "Eugene",          "state": "OR", "county": "Lane"},
        {"city": "Gresham",         "state": "OR", "county": "Multnomah"},
        {"city": "Hillsboro",       "state": "OR", "county": "Washington"},
        {"city": "Bend",            "state": "OR", "county": "Deschutes"},
        {"city": "Medford",         "state": "OR", "county": "Jackson"},
        # Pennsylvania
        {"city": "Philadelphia",    "state": "PA", "county": "Philadelphia"},
        {"city": "Pittsburgh",      "state": "PA", "county": "Allegheny"},
        {"city": "Allentown",       "state": "PA", "county": "Lehigh"},
        {"city": "Erie",            "state": "PA", "county": "Erie"},
        {"city": "Reading",         "state": "PA", "county": "Berks"},
        {"city": "Scranton",        "state": "PA", "county": "Lackawanna"},
        {"city": "Bethlehem",       "state": "PA", "county": "Northampton"},
        # Rhode Island
        {"city": "Providence",      "state": "RI", "county": "Providence"},
        {"city": "Cranston",        "state": "RI", "county": "Providence"},
        {"city": "Warwick",         "state": "RI", "county": "Kent"},
        {"city": "Pawtucket",       "state": "RI", "county": "Providence"},
        {"city": "Woonsocket",      "state": "RI", "county": "Providence"},
        # South Carolina
        {"city": "Columbia",        "state": "SC", "county": "Richland"},
        {"city": "Charleston",      "state": "SC", "county": "Charleston"},
        {"city": "North Charleston","state": "SC", "county": "Charleston"},
        {"city": "Mount Pleasant",  "state": "SC", "county": "Charleston"},
        {"city": "Rock Hill",       "state": "SC", "county": "York"},
        {"city": "Greenville",      "state": "SC", "county": "Greenville"},
        {"city": "Spartanburg",     "state": "SC", "county": "Spartanburg"},
        # South Dakota
        {"city": "Sioux Falls",     "state": "SD", "county": "Minnehaha"},
        {"city": "Rapid City",      "state": "SD", "county": "Pennington"},
        {"city": "Aberdeen",        "state": "SD", "county": "Brown"},
        {"city": "Brookings",       "state": "SD", "county": "Brookings"},
        {"city": "Watertown",       "state": "SD", "county": "Codington"},
        # Tennessee
        {"city": "Nashville",       "state": "TN", "county": "Davidson"},
        {"city": "Memphis",         "state": "TN", "county": "Shelby"},
        {"city": "Knoxville",       "state": "TN", "county": "Knox"},
        {"city": "Chattanooga",     "state": "TN", "county": "Hamilton"},
        {"city": "Clarksville",     "state": "TN", "county": "Montgomery"},
        {"city": "Murfreesboro",    "state": "TN", "county": "Rutherford"},
        {"city": "Jackson",         "state": "TN", "county": "Madison"},
        # Texas
        {"city": "Houston",         "state": "TX", "county": "Harris"},
        {"city": "San Antonio",     "state": "TX", "county": "Bexar"},
        {"city": "Dallas",          "state": "TX", "county": "Dallas"},
        {"city": "Austin",          "state": "TX", "county": "Travis"},
        {"city": "Fort Worth",      "state": "TX", "county": "Tarrant"},
        {"city": "El Paso",         "state": "TX", "county": "El Paso"},
        {"city": "Arlington",       "state": "TX", "county": "Tarrant"},
        {"city": "Corpus Christi",  "state": "TX", "county": "Nueces"},
        {"city": "Lubbock",         "state": "TX", "county": "Lubbock"},
        {"city": "Garland",         "state": "TX", "county": "Dallas"},
        # Utah
        {"city": "Salt Lake City",  "state": "UT", "county": "Salt Lake"},
        {"city": "West Valley City","state": "UT", "county": "Salt Lake"},
        {"city": "Provo",           "state": "UT", "county": "Utah"},
        {"city": "West Jordan",     "state": "UT", "county": "Salt Lake"},
        {"city": "Orem",            "state": "UT", "county": "Utah"},
        {"city": "Ogden",           "state": "UT", "county": "Weber"},
        {"city": "Layton",          "state": "UT", "county": "Davis"},
        # Vermont
        {"city": "Burlington",      "state": "VT", "county": "Chittenden"},
        {"city": "South Burlington","state": "VT", "county": "Chittenden"},
        {"city": "Rutland",         "state": "VT", "county": "Rutland"},
        {"city": "Essex Junction",  "state": "VT", "county": "Chittenden"},
        {"city": "Montpelier",      "state": "VT", "county": "Washington"},
        # Virginia
        {"city": "Virginia Beach",  "state": "VA", "county": "Virginia Beach City"},
        {"city": "Norfolk",         "state": "VA", "county": "Norfolk City"},
        {"city": "Chesapeake",      "state": "VA", "county": "Chesapeake City"},
        {"city": "Richmond",        "state": "VA", "county": "Richmond City"},
        {"city": "Newport News",    "state": "VA", "county": "Newport News City"},
        {"city": "Alexandria",      "state": "VA", "county": "Alexandria City"},
        {"city": "Hampton",         "state": "VA", "county": "Hampton City"},
        {"city": "Roanoke",         "state": "VA", "county": "Roanoke City"},
        # Washington
        {"city": "Seattle",         "state": "WA", "county": "King"},
        {"city": "Spokane",         "state": "WA", "county": "Spokane"},
        {"city": "Tacoma",          "state": "WA", "county": "Pierce"},
        {"city": "Vancouver",       "state": "WA", "county": "Clark"},
        {"city": "Bellevue",        "state": "WA", "county": "King"},
        {"city": "Kennewick",       "state": "WA", "county": "Benton"},
        {"city": "Renton",          "state": "WA", "county": "King"},
        # West Virginia
        {"city": "Charleston",      "state": "WV", "county": "Kanawha"},
        {"city": "Huntington",      "state": "WV", "county": "Cabell"},
        {"city": "Morgantown",      "state": "WV", "county": "Monongalia"},
        {"city": "Parkersburg",     "state": "WV", "county": "Wood"},
        {"city": "Wheeling",        "state": "WV", "county": "Ohio"},
        # Wisconsin
        {"city": "Milwaukee",       "state": "WI", "county": "Milwaukee"},
        {"city": "Madison",         "state": "WI", "county": "Dane"},
        {"city": "Green Bay",       "state": "WI", "county": "Brown"},
        {"city": "Kenosha",         "state": "WI", "county": "Kenosha"},
        {"city": "Racine",          "state": "WI", "county": "Racine"},
        {"city": "Appleton",        "state": "WI", "county": "Outagamie"},
        {"city": "Waukesha",        "state": "WI", "county": "Waukesha"},
        # Wyoming
        {"city": "Cheyenne",        "state": "WY", "county": "Laramie"},
        {"city": "Casper",          "state": "WY", "county": "Natrona"},
        {"city": "Laramie",         "state": "WY", "county": "Albany"},
        {"city": "Gillette",        "state": "WY", "county": "Campbell"},
        {"city": "Rock Springs",    "state": "WY", "county": "Sweetwater"},
    ],
    "business_name": "Wholesale Omniverse",
    "phone": "207-385-4041",
    "email": "WholesaleOmniverse@gmail.com",
    "paypal_me": "paypal.me/wholesaleomniverse",
}

# Motivation phrases rotated by city index to avoid duplicate content penalties
MOTIVATION_HOOKS = [
    "behind on payments",
    "going through a divorce",
    "inherited an unwanted property",
    "facing foreclosure",
    "relocating out of state",
    "tired of dealing with tenants",
    "property needs major repairs",
    "going through probate",
    "underwater on your mortgage",
    "dealing with tax liens",
]

FAQS = [
    ("How fast can you close?",
     "We can close in as little as 7–14 days — or on your timeline if you need more time."),
    ("Do I need to make repairs?",
     "No. We buy houses as-is. You don't need to fix a single thing."),
    ("Are there any fees or commissions?",
     "Zero. We're not agents. There are no commissions, no closing costs on your end."),
    ("How do you determine the offer price?",
     "We look at recent sales in your neighborhood, the condition of the property, "
     "and what repairs are needed. We then make you a fair, no-obligation cash offer."),
    ("What if I owe more than the house is worth?",
     "We can still help. We work with homeowners in all situations, including short sales."),
    ("Is my information kept private?",
     "Absolutely. Your information is never shared with third parties."),
]


def _slug(city: str, state: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"we-buy-houses-{city}-{state}".lower()).strip("-")


def _title(city: str, state: str) -> str:
    return f"We Buy Houses {city}, {state} — Fast Cash Offers, No Fees"


def _meta_desc(city: str, state: str, biz: str) -> str:
    return (
        f"Sell your house fast in {city}, {state}. {biz} buys homes as-is for cash. "
        f"No repairs, no commissions, no hassle. Get a free offer in 24 hours."
    )


def _page_html(market: dict, config: dict, hook: str) -> str:
    city    = market["city"]
    state   = market["state"]
    county  = market.get("county", "")
    biz     = config["business_name"]
    phone   = config["phone"]
    email   = config["email"]
    title   = _title(city, state)
    meta    = _meta_desc(city, state, biz)
    slug    = _slug(city, state)
    keyword = f"we buy houses {city} {state}"

    faq_html = "\n".join(
        f'  <details><summary>{q}</summary><p>{a}</p></details>'
        for q, a in FAQS
    )

    county_line = f", {county} County" if county else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title}</title>
  <meta name="description" content="{meta}">
  <meta name="keywords" content="{keyword}, sell my house fast {city}, cash home buyers {city} {state}, {county} home buyers">
  <link rel="canonical" href="https://kodelyoko1.github.io/Kode-Claude/{slug}.html">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{meta}">
  <meta property="og:url" content="https://kodelyoko1.github.io/Kode-Claude/{slug}.html">
  <meta property="og:type" content="website">
  <meta property="og:image" content="https://kodelyoko1.github.io/Kode-Claude/assets/logo.png">
  <script type="application/ld+json">
  {{
    "@context": "https://schema.org",
    "@type": "RealEstateAgent",
    "name": "Wholesale Omniverse — We Buy Houses in {city}, {state}",
    "description": "{meta}",
    "url": "https://kodelyoko1.github.io/Kode-Claude/{slug}.html",
    "telephone": "{phone}",
    "email": "{email}",
    "areaServed": {{
      "@type": "City",
      "name": "{city}",
      "containedInPlace": {{"@type": "State", "addressCountry": "US"}}
    }},
    "address": {{
      "@type": "PostalAddress",
      "addressLocality": "{city}",
      "addressRegion": "{state}",
      "addressCountry": "US"
    }},
    "priceRange": "Cash offers — no fees",
    "paymentAccepted": "Cash",
    "openingHours": "Mo-Su 08:00-20:00"
  }}
  </script>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:'Segoe UI',sans-serif;color:#1a1a1a;background:#fff}}
    header{{background:#1a3c6e;color:#fff;padding:2rem 1rem;text-align:center}}
    header h1{{font-size:2rem;margin-bottom:.5rem}}
    header p{{font-size:1.1rem;opacity:.9}}
    .cta-bar{{background:#e8a020;padding:1.2rem;text-align:center}}
    .cta-bar a{{color:#fff;font-size:1.3rem;font-weight:700;text-decoration:none}}
    .section{{max-width:860px;margin:2.5rem auto;padding:0 1rem}}
    h2{{color:#1a3c6e;margin-bottom:1rem;font-size:1.5rem}}
    .steps{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:1.5rem;margin:1.5rem 0}}
    .step{{background:#f4f7fb;border-radius:8px;padding:1.5rem;text-align:center}}
    .step .num{{font-size:2rem;font-weight:700;color:#e8a020}}
    .benefits li{{margin:.6rem 0;padding-left:1.5rem;position:relative}}
    .benefits li::before{{content:"✓";position:absolute;left:0;color:#27ae60;font-weight:700}}
    details{{border:1px solid #ddd;border-radius:6px;padding:.8rem 1rem;margin:.6rem 0}}
    summary{{cursor:pointer;font-weight:600;color:#1a3c6e}}
    details p{{margin-top:.6rem;color:#444}}
    footer{{background:#1a3c6e;color:#aac;text-align:center;padding:1.5rem;margin-top:3rem;font-size:.85rem}}
  </style>
</head>
<body>

<header>
  <h1>We Buy Houses in {city}, {state}</h1>
  <p>Fast cash offers — close in 7–14 days — zero fees, zero repairs needed</p>
</header>

<div class="cta-bar">
  <a href="tel:{phone}">📞 Call or Text Now: {phone}</a>
</div>

<div class="section">
  <h2>Get a Fair Cash Offer for Your {city}{county_line} Home</h2>
  <p>
    Are you {hook}? {biz} buys houses in {city} and throughout {state} — in any
    condition, any situation. We pay cash, cover closing costs, and close on
    <em>your</em> timeline — sometimes in as little as 7 days.
  </p>
</div>

<div class="section">
  <h2>How It Works</h2>
  <div class="steps">
    <div class="step"><div class="num">1</div><strong>Contact Us</strong><br>Call, text, or email us about your property.</div>
    <div class="step"><div class="num">2</div><strong>Get an Offer</strong><br>We evaluate and send a no-obligation cash offer within 24 hrs.</div>
    <div class="step"><div class="num">3</div><strong>Choose Your Date</strong><br>Pick any closing date. We handle the paperwork.</div>
    <div class="step"><div class="num">4</div><strong>Get Paid</strong><br>Cash in hand at closing. Simple as that.</div>
  </div>
</div>

<div class="section">
  <h2>Why Homeowners in {city} Choose {biz}</h2>
  <ul class="benefits">
    <li>No repairs, no cleaning — sell it exactly as-is</li>
    <li>No agent commissions (save 5–6%)</li>
    <li>No lender delays — we pay cash</li>
    <li>Close in 7 days or whenever you're ready</li>
    <li>We handle all paperwork and closing costs</li>
    <li>Local {state} investors — not a national chain</li>
  </ul>
</div>

<div class="section">
  <h2>We Buy All Types of {city} Properties</h2>
  <ul class="benefits">
    <li>Single-family homes</li>
    <li>Multi-family / duplexes</li>
    <li>Inherited or estate properties</li>
    <li>Rental properties (even with tenants)</li>
    <li>Distressed or fire-damaged homes</li>
    <li>Properties with code violations or liens</li>
  </ul>
</div>

<div class="section">
  <h2>Frequently Asked Questions</h2>
  <div class="faq">
{faq_html}
  </div>
</div>

<div class="section" id="offer" style="background:#1a3c6e;padding:2.5rem 1rem;border-radius:10px;color:#fff">
  <h2 style="color:#e8a020;text-align:center">Get Your Free Cash Offer Today</h2>
  <p style="text-align:center;opacity:.85;margin:.5rem 0 1.5rem">No obligation · No repairs · Close in 7 days</p>
  <form action="https://formsubmit.co/{email}" method="POST"
        style="max-width:480px;margin:0 auto;display:grid;gap:.75rem">
    <input type="hidden" name="_subject" value="New Cash Offer Request — {city}, {state}">
    <input type="hidden" name="_next"    value="https://kodelyoko1.github.io/Kode-Claude/thank-you.html">
    <input type="hidden" name="_captcha" value="false">
    <input type="hidden" name="market"   value="{city}, {state}">
    <input type="text"  name="name"     placeholder="Your Name"         required
           style="padding:.75rem;border-radius:6px;border:none;font-size:1rem">
    <input type="tel"   name="phone"    placeholder="Phone Number"       required
           style="padding:.75rem;border-radius:6px;border:none;font-size:1rem">
    <input type="email" name="email"    placeholder="Email Address"
           style="padding:.75rem;border-radius:6px;border:none;font-size:1rem">
    <input type="text"  name="address"  placeholder="Property Address"  required
           style="padding:.75rem;border-radius:6px;border:none;font-size:1rem">
    <select name="timeline"
            style="padding:.75rem;border-radius:6px;border:none;font-size:1rem;color:#555">
      <option value="">How soon do you need to sell?</option>
      <option>ASAP — within 2 weeks</option>
      <option>1–3 months</option>
      <option>3–6 months</option>
      <option>Just exploring options</option>
    </select>
    <button type="submit"
            style="background:#e8a020;color:#fff;border:none;padding:.9rem;border-radius:6px;
                   font-size:1.1rem;font-weight:700;cursor:pointer">
      Request My Cash Offer →
    </button>
  </form>
  <p style="text-align:center;margin-top:1rem;opacity:.7;font-size:.9rem">
    Or call/text: <a href="tel:{phone}" style="color:#e8a020;font-weight:700">{phone}</a>
  </p>
</div>

<footer>
  &copy; {datetime.now().year} {biz} — Cash Home Buyers in {city}, {state} and throughout {state}.
  | <a href="mailto:{email}" style="color:#aac">{email}</a>
  | <a href="tel:{phone}" style="color:#aac">{phone}</a>
</footer>

</body>
</html>"""


def _page_md(market: dict, config: dict, hook: str) -> str:
    city  = market["city"]
    state = market["state"]
    biz   = config["business_name"]
    phone = config["phone"]
    email = config["email"]

    faq_md = "\n".join(f"**{q}**\n{a}\n" for q, a in FAQS)

    return f"""# We Buy Houses {city}, {state} — Fast Cash Offers, No Fees

*{biz} | {phone} | {email}*

---

Are you {hook}? {biz} buys houses in {city}, {state} — any condition, any situation.
We pay cash, cover closing costs, and close on your timeline.

## How It Works

1. **Contact Us** — Call, text, or email about your property.
2. **Get an Offer** — No-obligation cash offer within 24 hours.
3. **Choose Your Closing Date** — 7 days or whenever you're ready.
4. **Get Paid** — Cash at closing, all paperwork handled.

## Why Choose {biz}?

- No repairs, no cleaning — sell as-is
- Zero agent commissions
- No lender delays — cash purchase
- Local {state} investors

## FAQ

{faq_md}

---

📞 **{phone}** | ✉ **{email}**
"""


def run_full_cycle() -> dict:
    config = storage.load("pseo_config.json", DEFAULT_CONFIG)
    markets = config.get("markets", DEFAULT_CONFIG["markets"])

    index = storage.load("pseo_index.json", {})
    pages_built = 0
    pages_skipped = 0

    for i, market in enumerate(markets):
        city  = market.get("city", "")
        state = market.get("state", "")
        if not city or not state:
            continue

        slug = _slug(city, state)
        hook = MOTIVATION_HOOKS[i % len(MOTIVATION_HOOKS)]

        html_path = PAGES_DIR / f"{slug}.html"
        md_path   = PAGES_DIR / f"{slug}.md"

        html_content = _page_html(market, config, hook)
        md_content   = _page_md(market, config, hook)

        html_path.write_text(html_content, encoding="utf-8")
        md_path.write_text(md_content,   encoding="utf-8")

        index[slug] = {
            "slug":       slug,
            "city":       city,
            "state":      state,
            "county":     market.get("county", ""),
            "html_path":  str(html_path),
            "md_path":    str(md_path),
            "keyword":    f"we buy houses {city} {state}",
            "built_at":   datetime.now(timezone.utc).isoformat(),
        }
        pages_built += 1

    storage.save("pseo_index.json", index)

    rev  = billing.revenue_summary(AGENT_KEY)
    subs = storage.load("pseo_subscribers.json", [])
    metrics.record(
        AGENT_KEY,
        pages_built=pages_built,
        total_pages=len(index),
        active_subs=sum(1 for s in subs if s.get("status") == "active"),
        mrr=rev["mrr"],
    )

    return {
        "pages_built":   pages_built,
        "pages_skipped": pages_skipped,
        "total_pages":   len(index),
        "output_dir":    str(PAGES_DIR),
        "mrr":           rev["mrr"],
    }

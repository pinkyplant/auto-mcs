from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher
from zipfile import ZipFile
from copy import deepcopy
from glob import glob
import constants
import requests
import hashlib
import json
import os
import re


# Auto-MCS Add-on API
# ----------------------------------------------- Addon Objects --------------------------------------------------------

# Base AddonObject for others
class AddonObject():
    def __init__(self):
        self.addon_object_type = None
        self.name = None
        self.type = None
        self.author = None
        self.subtitle = None
        self.id = None
        self.url = None
        self.addon_version = None

# AddonObject for housing downloadable addons
class AddonWebObject(AddonObject):
    def __init__(self, addon_name, addon_type, addon_author, addon_subtitle, addon_url, addon_id, addon_version):
        super().__init__()
        self.addon_object_type = "web"
        self.name = addon_name
        self.type = addon_type
        self.author = addon_author
        self.subtitle = addon_subtitle
        self.url = addon_url
        self.id = addon_id
        self.addon_version = addon_version

        # To be updated in get_addon_info()
        self.supported = "unknown"
        self.versions = []
        self.description = None
        self.download_url = None
        self.download_version = None

# AddonObject for housing imported addons
class AddonFileObject(AddonObject):
    def __init__(self, addon_name, addon_type, addon_author, addon_subtitle, addon_path, addon_id, addon_version):
        super().__init__()
        self.addon_object_type = "file"
        self.name = addon_name
        self.type = addon_type
        self.author = addon_author
        self.subtitle = addon_subtitle
        self.id = addon_id
        self.path = addon_path
        self.addon_version = addon_version
        self.disabled = False

        # Generate Hash
        hash_data = int(hashlib.md5(f'{os.path.getsize(addon_path)}/{os.path.basename(addon_path)}'.encode()).hexdigest(), 16)
        self.hash = str(hash_data)[:8]

# Server addon manager object for ServerManager()
class AddonManager():

    def __init__(self, server_name: str):
        self.server = dump_config(server_name)
        self.installed_addons = enumerate_addons(self.server)
        self.geyser_support = self.check_geyser()
        self.addon_hash = self.set_hash()
        self.update_required = False
        self.update_notified = False

        # Set addon hash if server is running
        try:
            if self.server['name'] in constants.server_manager.running_servers:
                constants.server_manager.running_servers['name'].run_data['addon-hash'] = deepcopy(self.addon_hash)
        except:
            pass

        # Write addons to cache
        constants.load_addon_cache(True)

    # Refreshes self.installed_addons list
    def refresh_addons(self):
        self.server = dump_config(self.server['name'])
        self.installed_addons = enumerate_addons(self.server)
        self.geyser_support = self.check_geyser()
        self.addon_hash = self.set_hash()

    # Imports addon directly from file path
    def import_addon(self, addon_path: str):
        addon = get_addon_file(addon_path, self.server)
        import_addon(addon, self.server)
        self.refresh_addons()
        return addon

    # Downloads addon directly from the closest match of name, or from AddonWebObject
    def download_addon(self, addon: str or AddonWebObject):

        # If AddonWebObject was provided
        if isinstance(addon, AddonWebObject):
            if not addon.download_url:
                addon = get_addon_url(addon, self.server)
            if addon:
                download_addon(addon, self.server)

        # If addon was provided with a name
        else:
            addon = find_addon(addon, self.server)
            if addon:
                download_addon(addon, self.server)
        self.refresh_addons()

    # Enables/Disables installed addons
    def addon_state(self, addon: AddonFileObject, enabled=True):
        addon_state(addon, self.server, enabled)
        self.refresh_addons()

    # Deletes addon
    def delete_addon(self, addon: AddonFileObject):
        try:
            os.remove(addon.path)
            removed = True
        except OSError:
            removed = False

        self.refresh_addons()
        return removed

    # Checks if an update is available for any AddonFileObject
    def check_for_updates(self):
        if self.update_required:
            return True

        # print("Checking for updates!!!")
        if constants.app_online:
            for addon in self.installed_addons['enabled']:
                try:
                    update = get_update_url(addon, self.server['version'], self.server['type'])
                    if constants.check_app_version(addon.addon_version, update.addon_version):
                        print(addon.name, addon.addon_version, update.addon_version)
                        self.update_required = True
                        return True
                except:
                    continue

        return False

    # Returns single list of all addons
    def return_single_list(self):
        return enumerate_addons(self.server, True)

    # Returns bool of geyser installation
    def check_geyser(self):

        if self.server['type'] in ['spigot', 'paper', 'fabric']:

            # Check for geyser
            return 'geyser' in [addon.id.lower() for addon in self.return_single_list()]
        else:
            return False

    # Sets addon hash to determine changes
    def set_hash(self):
        addon_hash = ""

        for addon in sorted(self.installed_addons['enabled'], key=lambda x: x.name):
            addon_hash += addon.hash

        return addon_hash

    # Checks addon hash in running config to see if it's changed
    def hash_changed(self):
        hash_changed = False
        server_name = self.server['name']

        if server_name in constants.server_manager.running_servers:
            hash_changed = constants.server_manager.running_servers[server_name].run_data['addon-hash'] != self.addon_hash

        return hash_changed


# -------------------------------------------- Addon File Functions ----------------------------------------------------

# Returns file object from addon jar file
# addon.jar --> AddonFileObject
def get_addon_file(addon_path: str, server_properties, enabled=False):
    jar_name = os.path.basename(addon_path)
    addon_name = None
    addon_author = None
    addon_subtitle = None
    addon_version = None
    addon_type = None
    addon_id = None
    cached = False


    # Determine which addons to look for
    if server_properties['type'] in ["craftbukkit", "paper", "spigot"]:
        server_type = "bukkit"
    else:
        server_type = server_properties['type']


    # Get addon information
    if jar_name.endswith(".jar"):

        # First, check if plugin is cached
        hash_data = int(hashlib.md5(f'{os.path.getsize(addon_path)}/{os.path.basename(addon_path)}'.encode()).hexdigest(), 16)
        hash_data = str(hash_data)[:8]
        if hash_data in constants.addon_cache.keys():
            cached = constants.addon_cache[hash_data]
            addon_name = cached['name']
            addon_type = cached['type']
            addon_author = cached['author']
            addon_subtitle = cached['subtitle']
            addon_id = cached['id']
            addon_version = cached['addon_version']


        # Next, check plugin.yml if it exists
        else:
            try:
                with ZipFile(addon_path, 'r') as jar_file:
                    addon_tmp = os.path.join(constants.tempDir, constants.gen_rstring(6))
                    constants.folder_check(addon_tmp)

                    # Check if addon is actually a bukkit plugin
                    if server_type == "bukkit":
                        try:
                            jar_file.extract('plugin.yml', addon_tmp)
                            with open(os.path.join(addon_tmp, 'plugin.yml'), 'r') as yml:
                                addon_type = server_type
                                next_line_desc = False
                                for line in yml.readlines():
                                    if next_line_desc:
                                        addon_subtitle = line.replace("\"", "").strip()
                                        next_line_desc = False
                                    elif addon_author and addon_name and addon_version and addon_subtitle and addon_id:
                                        break
                                    elif line.strip().startswith("name:"):
                                        addon_name = line.split("name:")[1].replace("\"", "").strip()
                                    elif line.strip().startswith("author:"):
                                        addon_author = line.split("author:")[1].replace("\"", "").strip()
                                    elif line.strip().startswith("main:"):
                                        if not addon_author:
                                            if "com" in line:
                                                try:
                                                    addon_author = line.split("com.")[1].split(".")[0].replace("\"", "").strip()
                                                except IndexError:
                                                    addon_author = line.split(".")[1].replace("\"", "").strip()
                                            else:
                                                addon_author = line.split(".")[1].replace("\"", "").strip()
                                        try:
                                            addon_id = line.split(".")[2].replace("\"", "").strip().lower()
                                        except IndexError:
                                            if line.startswith("main:"):
                                                addon_id = line.split(".")[0].split(":")[1].strip().lower()
                                            else:
                                                addon_id = addon_name.lower().replace(" ", "-")
                                    elif line.strip().startswith("description:"):
                                        addon_subtitle = line.split("description:")[1].replace("\"", "").strip()
                                        next_line_desc = addon_subtitle == ">"
                                    elif line.strip().startswith("version:"):
                                        addon_version = line.split("version:")[1].replace("\"", "").replace("-", " ").strip()
                                        if "+" in addon_version:
                                            addon_version = addon_version.split("+")[0]
                                        if ";" in addon_version:
                                            addon_version = addon_version.split(";")[0]
                        except KeyError:
                            pass


                    # Check if addon is actually a forge mod
                    elif server_type == "forge":

                        # Check if mcmod.info exists
                        try:
                            jar_file.extract('mcmod.info', addon_tmp)
                            with open(os.path.join(addon_tmp, 'mcmod.info'), 'r') as info:
                                addon_type = server_type
                                for line in info.readlines():
                                    if addon_author and addon_name and addon_version and addon_subtitle and addon_id:
                                        break
                                    elif line.strip().startswith("\"name\":"):
                                        addon_name = line.split("\"name\":")[1].replace("\"", "").replace(",", "").strip()
                                    elif line.strip().startswith("\"authorList\":"):
                                        addon_author = line.split("\"authorList\":")[1].replace("\"", "").replace("[", "").replace("]", "").strip()
                                        addon_author = addon_author[:-1] if addon_author.endswith(",") else addon_author
                                        addon_author = addon_author.split(',')[0].strip()
                                    elif line.strip().startswith("\"description\":"):
                                        addon_subtitle = line.split("\"description\":")[1].replace("\"", "").replace(",", "").strip()
                                    elif line.strip().startswith("\"modid\":"):
                                        addon_id = line.split("\"modid\":")[1].replace("\"", "").replace(",", "").strip().lower()
                                    elif line.strip().startswith("\"version\":"):
                                        addon_version = line.split("\"version\":")[1].replace("\"", "").replace(",", "").strip()
                                        if "+" in addon_version:
                                            addon_version = addon_version.split("+")[0]
                                        if ";" in addon_version:
                                            addon_version = addon_version.split(";")[0]
                        except KeyError:
                            pass

                        # If mcmod.info is absent, check mods.toml
                        if not addon_name:
                            try:
                                jar_file.extract('META-INF/mods.toml', addon_tmp)
                                with open(os.path.join(addon_tmp, 'META-INF', 'mods.toml'), 'r') as toml:
                                    addon_type = server_type
                                    file_contents = toml.read().split("[[dependencies")[0]
                                    for line in file_contents.splitlines():
                                        if addon_author and addon_name and addon_version and addon_subtitle and addon_id:
                                            break
                                        elif line.strip().startswith("displayName="):
                                            addon_name = line.split("displayName=")[1].replace("\"", "").strip()
                                        elif line.strip().startswith("modId="):
                                            addon_id = line.split("modId=")[1].replace("\"", "").replace(",", "").strip().lower()
                                        elif line.strip().startswith("authors="):
                                            addon_author = line.split("authors=")[1].replace("\"", "").strip()
                                            addon_author = addon_author.split(',')[0].strip()
                                        elif line.strip().startswith("version="):
                                            addon_version = line.split("version=")[1].replace("\"", "").replace("-", " ").strip()
                                            if "+" in addon_version:
                                                addon_version = addon_version.split("+")[0]
                                            if ";" in addon_version:
                                                addon_version = addon_version.split(";")[0]
                                    description = file_contents.split("description=")[1]
                                    if description:
                                        addon_subtitle = description.replace("'''", "").replace("\n", " ").strip().replace("- ", " ")
                            except KeyError:
                                pass


                    # Check if addon is actually a fabric mod
                    elif server_type == "fabric":
                        try:
                            jar_file.extract('fabric.mod.json', addon_tmp)
                            with open(os.path.join(addon_tmp, 'fabric.mod.json'), 'r') as mod:
                                addon_type = server_type
                                file_contents = json.loads(mod.read())

                                if file_contents['name']:
                                    addon_name = file_contents['name'].strip()
                                if file_contents['id']:
                                    addon_id = file_contents['id'].strip()
                                if file_contents['authors']:
                                    addon_author = file_contents['authors'][0].strip()
                                if file_contents['version']:
                                    addon_version = file_contents['version'].replace("\"", "").replace("-", " ").strip()
                                    if "+" in addon_version:
                                        addon_version = addon_version.split("+")[0].strip()
                                    if ";" in addon_version:
                                        addon_version = addon_version.split(";")[0].strip()
                                if file_contents['description']:
                                    addon_subtitle = file_contents['description'].replace("- ", " ").strip()
                        except KeyError:
                            pass


                    constants.safe_delete(addon_tmp)
            except:
                # Delete addon if it's corrupted
                try:
                    os.remove(addon_path)
                except OSError:
                    pass
                return None


            # If information was not found, use file name instead
            try:
                addon_version = re.search(r'\d+(\.\d+)+', addon_version).group(0)
            except:
                try:
                    addon_version = re.sub("[^0-9|.]", "", addon_version.split(' ')[0])
                except:
                    pass

            if not addon_name:

                new_name = jar_name.split(".jar")[0]
                if "- Copy" in new_name:
                    new_name = new_name.split("- Copy")[0]
                new_name = new_name.replace("-", " ")

                if " mod" in new_name or " Mod" in new_name:
                    new_name = new_name.split(" mod")[0].split(" Mod")[0]
                if " bukkit" in new_name or " Bukkit" in new_name:
                    new_name = new_name.split(" bukkit")[0].split(" Bukkit")[0]

                addon_name = new_name
                addon_type = server_type

        AddonObj = AddonFileObject(addon_name, addon_type, addon_author, addon_subtitle, addon_path, addon_id, addon_version)
        AddonObj.enabled = enabled

        # Create addon cache
        if not cached:
            size_name = str(os.path.getsize(addon_path)) + os.path.basename(addon_path)
            constants.addon_cache[AddonObj.hash] = {
                'name': addon_name,
                'type': addon_type,
                'author': addon_author,
                'subtitle': addon_subtitle,
                'id': addon_id,
                'addon_version': addon_version
            }

        return AddonObj
    else:
        return None


# Imports addon to server
# addon.jar --> AddonFileObject
def import_addon(addon_path: str or AddonFileObject, server_properties, tmpsvr=False):
    try:
        jar_name = os.path.basename(addon_path.path if isinstance(addon_path, AddonFileObject) else addon_path)
    except TypeError:
        return False

    addon_folder = "plugins" if server_properties['type'] in ['spigot', 'craftbukkit', 'paper'] else 'mods'
    destination_path = os.path.join(constants.tmpsvr, addon_folder) if tmpsvr else constants.server_path(server_properties['name'], addon_folder)

    # Make sure the addon_path and destination_path are not the same
    if addon_path != destination_path and jar_name.endswith(".jar"):

        # Convert addon_path into AddonFileObject
        if isinstance(addon_path, AddonFileObject):
            addon = addon_path
        else:
            addon = get_addon_file(addon_path, server_properties)

        # Copy addon to proper folder if it exists
        if addon:
            return constants.copy_to(addon.path, destination_path, str(constants.sanitize_name(addon.name, True) + ".jar"), overwrite=True)

    return False



# ------------------------------------------- Addon Web Functions ------------------------------------------------------

# Returns list of addon objects according to search
# Query --> AddonWebObject
def search_addons(query: str, server_properties):

    # Manually weighted search results
    prioritized = ("worldedit for bukkit", "vault", "essentials", "essentialsx", "worldguard", "anticheat", "zombie_striker_dev", "sleakes", "sk89q", "permissionsex", "multiverse-core", "shopkeepers")

    # filter-sort=5 is filtered by number of downloads
    search_urls = {
        "bukkit": "https://dev.bukkit.org/search?projects-page=1&projects-sort=-project&providerIdent=projects&search=",
        "forge": "https://modrinth.com/mod/",
        "fabric": "https://modrinth.com/mod/"
    }

    # Determine which addons to search for
    if server_properties['type'] in ["craftbukkit", "paper", "spigot"]:
        server_type = "bukkit"
    else:
        server_type = server_properties['type']


    # If server_type is bukkit
    if server_type == "bukkit":
        results_unsorted = []

        # Grab every addon from search result and return results dict
        url = search_urls[server_type] + query.replace(' ', '+')
        results = []
        page_content = constants.get_url(url)


        # Function to be used in ThreadPool
        def get_page(page_number):

            # Skip loading URL twice if page is already loaded
            if page_number == 1:
                new_page_content = page_content
            else:
                new_page_content = constants.get_url(url.replace('projects-page=1', f'projects-page={page_number}'))

            # Filter result content
            table = new_page_content.find('table', 'listing listing-project project-listing b-table b-table-a').find('tbody')
            # for row in table.find_all('tr', 'results'):
            for x, row in enumerate(table.find_all('tr', 'results'), 1):

                try:
                    date = row.find('abbr', 'tip standard-date standard-datetime').get('data-epoch')
                except AttributeError:
                    date = 0

                result_dict = {
                    'name': row.find('div', 'results-name').a.text.strip(),
                    'author': row.find('td', 'results-owner').a.text.strip(),
                    'subtitle': row.find('div', 'results-summary').text.strip(),
                    'link': row.find('div', 'results-name').a.get('href'),
                    'date': int(date),
                    'position': float(f"{page_number}.{x:04}")
                }

                # Manually weight popular plugins
                if result_dict['author'].lower() in prioritized or result_dict['name'].lower() in prioritized:
                    result_dict['position'] = 0.0

                results_unsorted.append(result_dict)

        # Get all pages
        try:
            pages = [int(item.text) for item in page_content.find('div', 'listing-header').find_all('a', 'b-pagination-item')]
            pages = 1 if not pages else max(pages)

            # If there's a single page, use already retrieved results
            if pages == 1:
                get_page(1)

            # If not, make a threadpool of total pages
            else:
                with ThreadPoolExecutor(max_workers=10) as pool:
                    pool.map(get_page, range(1, pages+1))

            # Sort list and add it to results
            for addon_dict in list(sorted(results_unsorted, key=lambda d: d['position'])):
                if addon_dict['link']:
                    results.append(AddonWebObject(addon_dict['name'], server_type, addon_dict['author'], addon_dict['subtitle'], addon_dict['link'], None, None))

        # If no results
        except AttributeError:
            pass


    # If server_type is forge or fabric
    else:
        # Grab every addon from search result and return results dict
        url = f'https://api.modrinth.com/v2/search?facets=[["categories:{server_type}"],["server_side:optional","server_side:required"]]&limit=100&query={query}'
        results = []
        page_content = constants.get_url(url, return_response=True).json()

        for mod in page_content['hits']:
            name = mod['title']
            author = mod['author']
            subtitle = mod['description'].split("\n", 1)[0]
            link = search_urls[server_type] + mod['slug']
            file_name = mod['slug']

            if link:
                addon_obj = AddonWebObject(name, server_type, author, subtitle, link, file_name, None)
                addon_obj.versions = [v for v in reversed(mod['versions']) if (v.startswith("1.") and "-" not in v)]
                results.append(addon_obj)


    return results


# Returns advanced addon object properties
# AddonWebObject
def get_addon_info(addon: AddonWebObject, server_properties):

    # For cleaning up description formatting
    emoji_pattern = re.compile("["
                               u"\U0001F600-\U0001F64F"  # emoticons
                               u"\U0001F300-\U0001F5FF"  # symbols & pictographs
                               u"\U0001F680-\U0001F6FF"  # transport & map symbols
                               u"\U0001F1E0-\U0001F1FF"  # flags (iOS)
                               "]+", flags=re.UNICODE)

    if addon.supported == "unknown":
        versions = []

        # Run specific actions for bukkit plugins
        if addon.type == "bukkit":

            # Find addon information
            item_link = constants.get_url(f"https://dev.bukkit.org{addon.url}", return_response=True).url
            file_link = f"{item_link}/files"

            # Replace numeric project id's with redirected string
            addon.id = item_link.split("/")[-1]
            addon.url = item_link.split("https://dev.bukkit.org")[1]

            with ThreadPoolExecutor(max_workers=10) as pool:
                addon_map = list(pool.map(constants.get_url, [item_link, file_link]))

            # Find description
            description = addon_map[0].find("div", "project-description").text
            description = emoji_pattern.sub(r'', description)  # no emoji
            description = re.sub(r'(\n\s*)+\n', '\n\n', description)

            # Get list of available versions
            select = addon_map[1].find('select', attrs={'name': 'filter-game-version'})

            try:
                select.find('option')
            except AttributeError:
                return None

            for option in select.findAll('option'):

                version = ""

                if addon.type == "bukkit":
                    if "1." in option.text.strip():
                        version = option.text.strip().replace("CB ", "").split("-")[0]
                else:
                    if option.text.strip().startswith("1."):
                        version = option.text.strip()

                if version not in versions and version:
                    versions.append(version)

            addon.versions = versions


        # Run specific actions for forge and fabric mods
        else:

            # Find addon information
            file_link = f"https://api.modrinth.com/v2/project/{addon.id}"
            page_content = constants.get_url(file_link, return_response=True).json()
            description = emoji_pattern.sub(r'', page_content['body']).replace("*","").replace("#","").replace('&nbsp;', ' ')
            description = '\n' + re.sub(r'(\n\s*)+\n', '\n\n', re.sub(r'<[^>]*>', '', description)).strip()
            description = re.sub(r'!?\[?\[(.+?)\]\(.*\)', lambda x: x.group(1), description).replace("![","")
            description = re.sub(r'\]\(*.+\)', '', description)

        server_version = server_properties["version"]
        addon.description = description
        addon.supported = "yes" if server_version in addon.versions else "no"

    return addon


# Return the latest available supported download link
# AddonWebObject
def get_addon_url(addon: AddonWebObject, server_properties, compat_mode=True):

    # Skip if addon doesn't exist for some reason
    if not addon:
        return False

    # Cleans up addon version from title
    def format_version(raw_version: str):
        try:
            raw_version = re.sub(r'(\[|\(|\{).*(\)|\]|\})', '', raw_version.lower())
            raw_version = raw_version.replace('beta', '.').replace('alpha', '.').replace('u', '.').replace('b','.').replace('a', '.')
            raw_version = re.sub("[^0-9|.]", "", raw_version)
            raw_version = re.search(r'\d+(\.\d+)+', raw_version).group(0)
            # print(addon_version)
        except:
            raw_version = None
        return raw_version

    # Instantiate required variables
    link_list = {}
    version_list = {}
    final_link = None
    final_version = None
    final_addon_version = None
    server_version = server_properties["version"]

    pages = 1

    # If addon is bukkit type, use alternate link
    if addon.type == "bukkit":

        # Iterate through every page until a match is found
        if not addon.id:
            item_link = constants.get_url(f"https://dev.bukkit.org{addon.url}", return_response=True).url

            # Replace numeric project id's with redirected string
            addon.id = item_link.split("/")[-1]
            addon.url = item_link.split("https://dev.bukkit.org")[1]

        item_link = f"https://dev.bukkit.org{addon.url}/files"
        page_content = constants.get_url(item_link)


        # Iterate through every page
        try:
            if page_content.find('div', 'listing-header').find('a', 'b-pagination-item'):
                pages = max([int(item.text) for item in page_content.find('div', 'listing-header').find_all('a', 'b-pagination-item')])
        except AttributeError:
            pass

        for page in range(1, pages + 1):

            if page != 1:
                item_link = f"https://dev.bukkit.org{addon.url}/files?page={page}"
                page_content = constants.get_url(item_link)

            # compile table into version & dl link
            table = page_content.find('table', attrs={'class': 'listing listing-project-file project-file-listing b-table b-table-a'})
            table_body = table.find('tbody')
            rows = table_body.find_all('tr')

            for row in rows:
                version = row.find('span', "version-label")  # game version
                link = row.find('a', attrs={'data-action': 'file-link'})

                try:
                    if "1." in version.text.strip():
                        version = version.text.strip().replace("CB ", "").split("-")[0]

                    # Add the latest version of each plugin to link list
                    if version not in link_list.keys() and isinstance(version, str):
                        download_url = f"https://dev.bukkit.org{link.get('href')}/download"

                        try:
                            addon_version = format_version(row.find(attrs={'data-name': True}).get('data-name'))
                        except:
                            addon_version = None

                        link_list[version] = download_url
                        version_list[version] = addon_version


                    # Handle mislabeled versions if proper version is in the title
                    if (server_version in link.text) and (server_version not in link_list.keys()) and (isinstance(version, str)) and compat_mode:

                        # Compare main build numbers to be sure this doesn't trigger from the addon version
                        version_a = version
                        if version.count(".") > 1:
                            version_a = version.rsplit('.', 1)[0]

                        version_b = server_version
                        if server_version.count(".") > 1:
                            version_b = server_version.rsplit('.', 1)[0]

                        if version_a == version_b:
                            download_url = f"https://dev.bukkit.org{link.get('href')}/download"
                            addon_version = format_version(link.text)
                            link_list[server_version] = download_url
                            version_list[server_version] = addon_version

                except ValueError:
                    pass

            if server_version in link_list:
                final_link = link_list[server_version]
                final_version = server_version
                final_addon_version = version_list[server_version]
                break


    # If addon type is forge or fabric
    else:

        # Iterate through every page until a match is found
        file_link = f'https://api.modrinth.com/v2/project/{addon.id}/version?loaders=["{addon.type}"]'
        page_content = constants.get_url(file_link, return_response=True).json()

        for data in page_content:
            files = data['files']
            url = data['files'][-1]['url']
            for version in data['game_versions']:
                if version not in link_list.keys() and version.startswith("1.") and "-" not in version:
                    addon_version = None
                    for gv in data['game_versions']:
                        gv_str = f'-{gv}-'
                        if gv_str in data['version_number']:
                            addon_version = format_version(data['version_number'].split(gv_str)[-1])
                            break
                    link_list[version] = url
                    version_list[version] = addon_version


    # In case a link is not found, find the latest compatible version
    if not final_link and compat_mode:

        for item in sorted(list(link_list.items()), key=lambda x: x[0], reverse=True):
            if constants.version_check(server_version, ">=", item[0]):
                final_link = item[1]
                final_version = item[0]
                final_addon_version = version_list[final_version]
                addon.supported = "no"
                break

    addon.download_url = final_link
    addon.download_version = final_version
    addon.addon_version = final_addon_version

    return addon


# Parse addon filename to find specific version
# AddonFileObject --> AddonWebObject
def get_update_url(addon: AddonFileObject, new_version: str, force_type=None):
    addon_url = None
    new_addon = None

    # Force type
    if force_type:
        if force_type.lower() in ['craftbukkit', 'spigot', 'paper']:
            new_type = 'bukkit'
        else:
            new_type = force_type.lower()
    else:
        new_type = addon.type

    # Possibly upgrade plugin to proper server type in case there's a mismatch

    project_urls = {
        "bukkit": "https://dev.bukkit.org/projects/",
        "forge": "https://modrinth.com/mod/",
        "fabric": "https://modrinth.com/mod/"
    }

    # First check if id is available
    if addon.id:
        potential_url = project_urls[new_type] + addon.id
        if constants.get_url(potential_url, return_response=True).status_code in [200, 302]:
            addon_url = potential_url

    # If id is absent, make a search for the name
    if addon.name and not addon_url:
        potential_url = project_urls[new_type] + addon.name
        if constants.get_url(potential_url, return_response=True).status_code in [200, 302]:
            addon_url = potential_url

    # If name is not a valid project link, use a search result
    if addon.name and not addon_url:
        addon_author = addon.author.lower() if addon.author else None
        addon_name = addon.name.lower() if addon.name else None

        addon_results = search_addons(addon.name.lower(), {"type": new_type})
        for result in addon_results:
            if (addon_author.lower() == result.author.lower()) and (addon_name.lower() == result.name.lower()) or (addon.id == result.id):
                new_addon = result if new_type != "bukkit" else get_addon_info(result, {"type": new_type, "version": new_version})
                addon_url = project_urls[result.type] + result.id
                break

    # Only return AddonWebObject if url was acquired
    if addon_url and not new_addon:
        addon_url = addon_url if new_type != "bukkit" else addon_url.split(".org")[1]
        new_addon = AddonWebObject(addon.name, new_type, addon.author, addon.subtitle, addon_url, addon.id, None)

    # If new_addon has an object, request download link
    if new_addon:
        new_addon = get_addon_url(new_addon, {"version": new_version, "type": new_type}, compat_mode=True)
        new_addon = new_addon if new_addon.download_url else None

    return new_addon


# Download web object into a jar file
# AddonWebObject --> addon.jar
def download_addon(addon: AddonWebObject, server_properties, tmpsvr=False):

    # Skip download if URL does not exist
    if not addon.download_url:
        return False

    addon_folder = "plugins" if server_properties['type'] in ['spigot', 'craftbukkit', 'paper'] else 'mods'
    destination_path = os.path.join(constants.tmpsvr, addon_folder) if tmpsvr else os.path.join(constants.server_path(server_properties['name']), addon_folder)

    # Download addon to "destination_path + file_name"
    file_name = constants.sanitize_name(addon.name if len(addon.name) < 35 else addon.name.split(' ')[0], True) + ".jar"
    total_path = os.path.join(destination_path, file_name)
    try:
        constants.cs_download_url(addon.download_url, file_name, destination_path)
    except requests.exceptions.SSLError:
        constants.download_url(addon.download_url, file_name, destination_path)

    # Check if addon is contained in a .zip file
    zip_file = False
    addon_download = os.path.join(constants.tempDir, "addon-download")
    with ZipFile(total_path, 'r') as jar_file:
        for item in [file for file in jar_file.namelist() if "/" not in file]:
            if item.endswith(".jar"):
                constants.folder_check(addon_download)
                jar_file.extract(item, addon_download)
                zip_file = True
                break

    if zip_file:
        new_file_name = glob(os.path.join(addon_download, "*.jar"))[0]
        os.remove(total_path)
        os.rename(new_file_name, total_path)
        constants.safe_delete(addon_download)

    return os.path.exists(total_path)


# Searches and returns downloadable addon
# str --> AddonWebObject
def find_addon(name, server_properties):
    try:
        new_addon = sorted([[addon, round(SequenceMatcher(None, addon.name.lower(), name.lower()).ratio(), 2)] for addon in search_addons(name, server_properties)], key=lambda x: x[1], reverse=True)[0][0]
    except IndexError:
        return False

    if new_addon:
        return get_addon_url(get_addon_info(new_addon, server_properties), server_properties)



# ------------------------------------------ Addon List Functions ------------------------------------------------------

# Creates a dictionary of enabled and disabled addons
# dict = {
#   'enabled': [AddonFileObject1, AddonFileObject2],
#   'disabled': [AddonFileObject3, AddonFileObject4]
# }
def enumerate_addons(server_properties, single_list=False):

    # Define folder paths based on server info
    addon_folder = "plugins" if server_properties['type'] in ['spigot', 'craftbukkit', 'paper'] else 'mods'
    disabled_addon_folder = str("disabled-" + addon_folder)
    addon_folder = constants.server_path(server_properties['name'], addon_folder)
    disabled_addon_folder = constants.server_path(server_properties['name'], disabled_addon_folder)

    enabled_addons = []
    disabled_addons = []

    # Get list of enabled AddonFileObjects
    if addon_folder:
        with ThreadPoolExecutor(max_workers=15) as pool:
            def enabled(addon, *a):
                addon = get_addon_file(addon, server_properties, enabled=True)
                if addon:
                    enabled_addons.append(addon)
            pool.map(enabled, glob(os.path.join(addon_folder, "*")))
        enabled_addons = list(filter(lambda item: item is not None, enabled_addons))

    if disabled_addon_folder:
        with ThreadPoolExecutor(max_workers=15) as pool:
            def disabled(addon, *a):
                addon = get_addon_file(addon, server_properties, enabled=False)
                if addon:
                    disabled_addons.append(addon)
            pool.map(disabled, glob(os.path.join(disabled_addon_folder, "*")))

    if single_list:
        new_list = constants.deepcopy(enabled_addons)
        new_list.extend(constants.deepcopy(disabled_addons))
        return new_list

    else:
        return {'enabled': enabled_addons, 'disabled': disabled_addons}


# Toggles addon state, alternate between normal and disabled folder
# AddonFileObject
def addon_state(addon: AddonFileObject, server_properties, enabled=True):

    # Define folder paths based on server info
    addon_folder = "plugins" if server_properties['type'] in ['spigot', 'craftbukkit', 'paper'] else 'mods'
    disabled_addon_folder = str("disabled-" + addon_folder)
    addon_folder = os.path.join(constants.server_path(server_properties['name']), addon_folder)
    disabled_addon_folder = os.path.join(constants.server_path(server_properties['name']), disabled_addon_folder)

    addon_path, addon_name = os.path.split(addon.path)

    # Enable addon if it's disabled
    if enabled and (addon_path == disabled_addon_folder):
        constants.folder_check(addon_folder)
        new_path = os.path.join(addon_folder, addon_name)
        os.rename(addon.path, new_path)
        addon.path = new_path

    # Enable addon if it's disabled
    elif not enabled and (addon_path == addon_folder):
        constants.folder_check(disabled_addon_folder)
        new_path = os.path.join(disabled_addon_folder, addon_name)
        os.rename(addon.path, new_path)
        addon.path = new_path

    return addon



# ---------------------------------------- Extraneous Addon Functions --------------------------------------------------

# name --> version, path
def dump_config(server_name: str):

    server_dict = {
        'name': server_name,
        'version': None,
        'type': None,
        'path': os.path.join(constants.applicationFolder, 'Servers', server_name)
    }


    config_file = constants.server_path(server_name, constants.server_ini)

    # Check auto-mcs.ini for info
    if config_file and os.path.isfile(config_file):
        server_config = constants.server_config(server_name)

        # Only pickup server as valid with good config
        if server_name == server_config.get("general", "serverName"):
            server_dict['version'] = server_config.get("general", "serverVersion")
            server_dict['type'] = server_config.get("general", "serverType").lower()


    return server_dict


# Returns chat reporting addon if it can be found
def disable_report_addon(server_properties):
    server_type = server_properties['type'].replace('craft','')

    item = None

    if server_type in ['spigot', 'bukkit', 'paper']:
        url = "https://modrinth.com/mod/freedomchat"

        # Find addon information
        html = constants.get_url(f"{url}/versions?g={server_properties['version']}&l={server_type}")
        item = html.find('div', class_='version-button')

        if not item:
            html = constants.get_url(f"{url}/versions?l={server_type}")
            item = html.find('div', class_='version-button')

    else:
        url = "https://modrinth.com/mod/no-chat-reports"

        # Find addon information
        html = constants.get_url(f"{url}/versions?g={server_properties['version']}&l={server_type}")
        item = html.find('div', class_='version-button')

        if not item:
            html = constants.get_url(f"{url}/versions?l={server_type}")
            item = html.find('div', class_='version-button')


    if item:
        if server_properties['type'] in ["craftbukkit", "paper", "spigot"]:
            server_type = "bukkit"
        else:
            server_type = server_properties['type']

        name = html.find('h1', class_='title').get_text()
        author = [x.div.p.text for x in html.find_all('a', class_='team-member') if 'owner' in x.get_text().lower()][0]
        subtitle = html.find('p', class_='description').get_text()
        link = item.a.get('href')
        file_name = name.lower().replace(" ", "-")

        item = AddonWebObject(name, server_type, author, subtitle, url, file_name, None)
        item.download_url = link

    return item


# Returns list of AddonWebObjects for Geyser
def geyser_addons(server_properties):
    final_list = []

    # Make AddonWebObjects for dependencies
    if server_properties['type'] in ['spigot', 'paper']:

        # Geyser bukkit
        url = 'https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/spigot'
        addon = AddonWebObject('Geyser', 'bukkit', 'GeyserMC', 'Bedrock packet compatibility layer', url, 'geyser', None)
        addon.download_url = url
        final_list.append(addon)

        # Floodgate bukkit
        url = 'https://download.geysermc.org/v2/projects/floodgate/versions/latest/builds/latest/downloads/spigot'
        addon = AddonWebObject('Floodgate', 'bukkit', 'GeyserMC', 'Bedrock account compatibility layer', url, 'floodgate', None)
        addon.download_url = url
        final_list.append(addon)

        # ViaVersion bukkit
        try:
            url = requests.get('https://api.github.com/repos/ViaVersion/ViaVersion/releases/latest').json()['assets'][-1]['browser_download_url']
            addon = AddonWebObject('ViaVersion', 'bukkit', 'ViaVersion', 'Allows newer clients to connect to legacy servers', url, 'viaversion', None)
            addon.download_url = url
            final_list.append(addon)
        except IndexError:
            pass


    elif server_properties['type'] == 'fabric':

        # Geyser fabric
        url = 'https://download.geysermc.org/v2/projects/geyser/versions/latest/builds/latest/downloads/fabric'
        addon = AddonWebObject('Geyser', 'fabric', 'GeyserMC', 'Bedrock packet compatibility layer', url, 'geyser', None)
        addon.download_url = url
        final_list.append(addon)

        # Floodgate fabric
        url = 'https://ci.opencollab.dev/job/GeyserMC/job/Floodgate-Fabric/job/master/lastSuccessfulBuild/artifact/build/libs/floodgate-fabric.jar'
        addon = AddonWebObject('Floodgate', 'fabric', 'GeyserMC', 'Bedrock account compatibility layer', url, 'floodgate', None)
        addon.download_url = url
        final_list.append(addon)

        # ViaVersion fabric
        # url = requests.get('https://api.github.com/repos/ViaVersion/ViaFabric/releases/latest').json()['assets'][-1]['browser_download_url']
        # addon = AddonWebObject('ViaFabric', 'fabric', 'ViaVersion', 'Allows newer clients to connect to legacy servers', url, 'viafabric')
        # addon.download_url = url
        # final_list.append(addon)


    return final_list



# ---------------------------------------------- Usage Examples --------------------------------------------------------

# properties = {"name": "Booger Squad", "type": "spigot", "version": "1.19"}

# # Search addon:
# try:
#     addon_search = search_addons("worldedit", properties)
#     addon_search[0] = get_addon_info(addon_search[0], properties)
#     addon_search[0] = get_addon_url(addon_search[0], properties, compat_mode=True)
#     success = download_addon(addon_search[0], os.path.split(jar_path)[0])
#     print(vars(addon_search[0]))
#
# except ConnectionRefusedError:
#     print("Cloudscraper failed")



# # Update addon: pass in (jar_path, server_properties, new_version)
# jar_path = r"C:\Users\...\WorldEdit.jar"
# try:
#     addon_file = get_addon_file(jar_path, properties)
#     addon_web = get_update_url(addon_file, '1.15')
#     success = download_addon(addon_web, properties, os.path.split(jar_path)[0])
#
#     # return (addon_web if addon_web else addon_file), success
#     print(vars(addon_web))
#
# except ConnectionRefusedError:
#     print("Cloudscraper failed")



# # Import addon:
# source_jar = r"C:\Users\...\worldedit - Copy (24).jar"
# import_addon(source_jar, properties)



# # Enumerate addons:
# addon_state(enumerate_addons(properties)['disabled'][0], properties, enabled=True)
# print([item.name for item in enumerate_addons(properties)['enabled']], [item.name for item in enumerate_addons(properties)['disabled']])

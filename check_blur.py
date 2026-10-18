import glob, cv2

from catdex_step1 import find_cat, blur_score

for path in glob.glob("cat_and_dog_mix/*.jpg"): # find every file ending in .jpg inside a folder called cat
    image = cv2.imread(path)
    print(path, round(blur_score(image)))

# glob searched relative to the folder my terminal is in
# must run script while in my project folder
"""ICD-10 code search — in-memory keyword fallback (pre-pgvector).

The architecture calls for semantic search via pgvector: embed ~200-300 codes,
embed the query, `ORDER BY embedding <=> query`. pgvector has no official Windows
binary and isn't installed on the local Postgres yet, so this module is the
documented FALLBACK: a curated in-memory catalog scored by keyword overlap.

The public shape (a `search(query, limit)` returning ranked `{code, description}`)
is deliberately the same one a pgvector-backed version would expose, so swapping
the implementation later is a drop-in — the route and frontend don't change.
"""

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.deps import get_current_user
from app.models import User

router = APIRouter(prefix="/icd", tags=["icd"])

# A small curated slice of common ICD-10 codes. In the pgvector version this is a
# DB table of ~200-300 rows with an embedding column; here it's an in-memory list.
ICD10_CATALOG: list[dict] = [
    # --- Certain infectious and parasitic diseases (A00-B99) ---
    {"code": "A08.4", "description": "Viral intestinal infection, unspecified"},
    {"code": "A09", "description": "Infectious gastroenteritis and colitis, unspecified"},
    {"code": "A41.9", "description": "Sepsis, unspecified organism"},
    {"code": "A49.9", "description": "Bacterial infection, unspecified"},
    {"code": "B00.9", "description": "Herpesviral infection, unspecified"},
    {"code": "B02.9", "description": "Zoster without complications"},
    {"code": "B20", "description": "Human immunodeficiency virus [HIV] disease"},
    {"code": "B34.9", "description": "Viral infection, unspecified"},
    {"code": "B35.1", "description": "Tinea unguium"},
    {"code": "B35.3", "description": "Tinea pedis"},
    {"code": "B35.4", "description": "Tinea corporis"},
    {"code": "B37.0", "description": "Candidal stomatitis"},
    {"code": "B37.3", "description": "Candidiasis of vulva and vagina"},
    {"code": "B99.9", "description": "Other and unspecified infectious diseases"},

    # --- Neoplasms (C00-D49) ---
    {"code": "C18.9", "description": "Malignant neoplasm of colon, unspecified"},
    {"code": "C34.90", "description": "Malignant neoplasm of unspecified part of unspecified bronchus or lung"},
    {"code": "C50.919", "description": "Malignant neoplasm of unspecified site of unspecified female breast"},
    {"code": "C61", "description": "Malignant neoplasm of prostate"},
    {"code": "C44.90", "description": "Unspecified malignant neoplasm of skin, unspecified"},
    {"code": "D12.6", "description": "Benign neoplasm of colon, unspecified"},
    {"code": "D25.9", "description": "Leiomyoma of uterus, unspecified"},
    {"code": "D17.9", "description": "Benign lipomatous neoplasm, unspecified"},

    # --- Diseases of the blood and blood-forming organs (D50-D89) ---
    {"code": "D64.9", "description": "Anemia, unspecified"},
    {"code": "D50.9", "description": "Iron deficiency anemia, unspecified"},
    {"code": "D51.0", "description": "Vitamin B12 deficiency anemia due to intrinsic factor deficiency"},
    {"code": "D62", "description": "Acute posthemorrhagic anemia"},
    {"code": "D69.6", "description": "Thrombocytopenia, unspecified"},
    {"code": "D72.829", "description": "Elevated white blood cell count, unspecified"},

    # --- Endocrine, nutritional and metabolic diseases (E00-E89) ---
    {"code": "E11.9", "description": "Type 2 diabetes mellitus without complications"},
    {"code": "E11.65", "description": "Type 2 diabetes mellitus with hyperglycemia"},
    {"code": "E11.22", "description": "Type 2 diabetes mellitus with diabetic chronic kidney disease"},
    {"code": "E11.40", "description": "Type 2 diabetes mellitus with diabetic neuropathy, unspecified"},
    {"code": "E11.21", "description": "Type 2 diabetes mellitus with diabetic nephropathy"},
    {"code": "E11.319", "description": "Type 2 diabetes mellitus with unspecified diabetic retinopathy without macular edema"},
    {"code": "E11.42", "description": "Type 2 diabetes mellitus with diabetic polyneuropathy"},
    {"code": "E11.51", "description": "Type 2 diabetes mellitus with diabetic peripheral angiopathy without gangrene"},
    {"code": "E11.8", "description": "Type 2 diabetes mellitus with unspecified complications"},
    {"code": "E10.9", "description": "Type 1 diabetes mellitus without complications"},
    {"code": "E10.65", "description": "Type 1 diabetes mellitus with hyperglycemia"},
    {"code": "E16.2", "description": "Hypoglycemia, unspecified"},
    {"code": "E78.5", "description": "Hyperlipidemia, unspecified"},
    {"code": "E66.9", "description": "Obesity, unspecified"},
    {"code": "E66.01", "description": "Morbid (severe) obesity due to excess calories"},
    {"code": "E03.9", "description": "Hypothyroidism, unspecified"},
    {"code": "E04.9", "description": "Nontoxic goiter, unspecified"},
    {"code": "E05.90", "description": "Thyrotoxicosis, unspecified, without thyrotoxic crisis or storm"},
    {"code": "E06.3", "description": "Autoimmune thyroiditis"},
    {"code": "E55.9", "description": "Vitamin D deficiency, unspecified"},
    {"code": "E86.0", "description": "Dehydration"},
    {"code": "E87.6", "description": "Hypokalemia"},
    {"code": "E87.1", "description": "Hypo-osmolality and hyponatremia"},
    {"code": "E83.42", "description": "Hypomagnesemia"},

    # --- Mental, behavioral and neurodevelopmental disorders (F01-F99) ---
    {"code": "F41.1", "description": "Generalized anxiety disorder"},
    {"code": "F41.9", "description": "Anxiety disorder, unspecified"},
    {"code": "F41.0", "description": "Panic disorder [episodic paroxysmal anxiety]"},
    {"code": "F32.9", "description": "Major depressive disorder, single episode, unspecified"},
    {"code": "F32.1", "description": "Major depressive disorder, single episode, moderate"},
    {"code": "F33.1", "description": "Major depressive disorder, recurrent, moderate"},
    {"code": "F33.9", "description": "Major depressive disorder, recurrent, unspecified"},
    {"code": "F31.9", "description": "Bipolar disorder, unspecified"},
    {"code": "F43.10", "description": "Post-traumatic stress disorder, unspecified"},
    {"code": "F43.23", "description": "Adjustment disorder with mixed anxiety and depressed mood"},
    {"code": "F42.9", "description": "Obsessive-compulsive disorder, unspecified"},
    {"code": "F90.9", "description": "Attention-deficit hyperactivity disorder, unspecified type"},
    {"code": "F90.0", "description": "Attention-deficit hyperactivity disorder, predominantly inattentive type"},
    {"code": "F17.210", "description": "Nicotine dependence, cigarettes, uncomplicated"},
    {"code": "F10.20", "description": "Alcohol dependence, uncomplicated"},
    {"code": "F10.10", "description": "Alcohol abuse, uncomplicated"},
    {"code": "F11.20", "description": "Opioid dependence, uncomplicated"},
    {"code": "F03.90", "description": "Unspecified dementia, unspecified severity, without behavioral disturbance"},

    # --- Diseases of the nervous system (G00-G99) ---
    {"code": "G43.909", "description": "Migraine, unspecified, not intractable, without status migrainosus"},
    {"code": "G43.109", "description": "Migraine with aura, not intractable, without status migrainosus"},
    {"code": "G44.209", "description": "Tension-type headache, unspecified, not intractable"},
    {"code": "G47.00", "description": "Insomnia, unspecified"},
    {"code": "G47.33", "description": "Obstructive sleep apnea (adult) (pediatric)"},
    {"code": "G40.909", "description": "Epilepsy, unspecified, not intractable, without status epilepticus"},
    {"code": "G20", "description": "Parkinson's disease"},
    {"code": "G30.9", "description": "Alzheimer's disease, unspecified"},
    {"code": "G35", "description": "Multiple sclerosis"},
    {"code": "G56.00", "description": "Carpal tunnel syndrome, unspecified upper limb"},
    {"code": "G62.9", "description": "Polyneuropathy, unspecified"},
    {"code": "G89.29", "description": "Other chronic pain"},
    {"code": "G89.4", "description": "Chronic pain syndrome"},
    {"code": "G45.9", "description": "Transient cerebral ischemic attack, unspecified"},

    # --- Diseases of the eye and adnexa (H00-H59) ---
    {"code": "H10.9", "description": "Unspecified conjunctivitis"},
    {"code": "H25.9", "description": "Unspecified age-related cataract"},
    {"code": "H40.9", "description": "Unspecified glaucoma"},
    {"code": "H52.4", "description": "Presbyopia"},
    {"code": "H52.13", "description": "Myopia, bilateral"},
    {"code": "H53.9", "description": "Unspecified visual disturbance"},
    {"code": "H57.9", "description": "Unspecified disorder of eye and adnexa"},
    {"code": "H00.019", "description": "Hordeolum externum unspecified eye, unspecified eyelid"},

    # --- Diseases of the ear and mastoid process (H60-H95) ---
    {"code": "H60.90", "description": "Unspecified otitis externa, unspecified ear"},
    {"code": "H61.20", "description": "Impacted cerumen, unspecified ear"},
    {"code": "H65.90", "description": "Unspecified nonsuppurative otitis media, unspecified ear"},
    {"code": "H66.90", "description": "Otitis media, unspecified, unspecified ear"},
    {"code": "H81.10", "description": "Benign paroxysmal vertigo, unspecified ear"},
    {"code": "H90.3", "description": "Sensorineural hearing loss, bilateral"},
    {"code": "H91.90", "description": "Unspecified hearing loss, unspecified ear"},
    {"code": "H92.09", "description": "Otalgia, unspecified ear"},

    # --- Diseases of the circulatory system (I00-I99) ---
    {"code": "I10", "description": "Essential (primary) hypertension"},
    {"code": "I11.9", "description": "Hypertensive heart disease without heart failure"},
    {"code": "I12.9", "description": "Hypertensive chronic kidney disease with stage 1 through stage 4 chronic kidney disease, or unspecified chronic kidney disease"},
    {"code": "I20.9", "description": "Angina pectoris, unspecified"},
    {"code": "I21.4", "description": "Non-ST elevation (NSTEMI) myocardial infarction"},
    {"code": "I21.9", "description": "Acute myocardial infarction, unspecified"},
    {"code": "I25.10", "description": "Atherosclerotic heart disease of native coronary artery without angina"},
    {"code": "I25.9", "description": "Chronic ischemic heart disease, unspecified"},
    {"code": "I25.2", "description": "Old myocardial infarction"},
    {"code": "I35.0", "description": "Nonrheumatic aortic (valve) stenosis"},
    {"code": "I47.10", "description": "Supraventricular tachycardia, unspecified"},
    {"code": "I48.91", "description": "Unspecified atrial fibrillation"},
    {"code": "I49.9", "description": "Cardiac arrhythmia, unspecified"},
    {"code": "I50.9", "description": "Heart failure, unspecified"},
    {"code": "I63.9", "description": "Cerebral infarction, unspecified"},
    {"code": "I73.9", "description": "Peripheral vascular disease, unspecified"},
    {"code": "I82.409", "description": "Acute embolism and thrombosis of unspecified deep veins of unspecified lower extremity"},
    {"code": "I83.90", "description": "Asymptomatic varicose veins of unspecified lower extremity"},
    {"code": "I87.2", "description": "Venous insufficiency (chronic) (peripheral)"},
    {"code": "I95.9", "description": "Hypotension, unspecified"},
    {"code": "I26.99", "description": "Other pulmonary embolism without acute cor pulmonale"},

    # --- Diseases of the respiratory system (J00-J99) ---
    {"code": "J45.909", "description": "Unspecified asthma, uncomplicated"},
    {"code": "J45.901", "description": "Unspecified asthma with (acute) exacerbation"},
    {"code": "J45.902", "description": "Unspecified asthma with status asthmaticus"},
    {"code": "J44.9", "description": "Chronic obstructive pulmonary disease, unspecified"},
    {"code": "J44.0", "description": "Chronic obstructive pulmonary disease with (acute) lower respiratory infection"},
    {"code": "J44.1", "description": "Chronic obstructive pulmonary disease with (acute) exacerbation"},
    {"code": "J43.9", "description": "Emphysema, unspecified"},
    {"code": "J47.9", "description": "Bronchiectasis, uncomplicated"},
    {"code": "J06.9", "description": "Acute upper respiratory infection, unspecified"},
    {"code": "J00", "description": "Acute nasopharyngitis [common cold]"},
    {"code": "J02.9", "description": "Acute pharyngitis, unspecified"},
    {"code": "J03.90", "description": "Acute tonsillitis, unspecified"},
    {"code": "J01.90", "description": "Acute sinusitis, unspecified"},
    {"code": "J32.9", "description": "Chronic sinusitis, unspecified"},
    {"code": "J20.9", "description": "Acute bronchitis, unspecified"},
    {"code": "J18.9", "description": "Pneumonia, unspecified organism"},
    {"code": "J15.9", "description": "Unspecified bacterial pneumonia"},
    {"code": "J12.9", "description": "Viral pneumonia, unspecified"},
    {"code": "J96.00", "description": "Acute respiratory failure, unspecified whether with hypoxia or hypercapnia"},
    {"code": "J30.9", "description": "Allergic rhinitis, unspecified"},
    {"code": "J30.1", "description": "Allergic rhinitis due to pollen (seasonal)"},
    {"code": "J30.2", "description": "Other seasonal allergic rhinitis"},

    # --- Diseases of the digestive system (K00-K95) ---
    {"code": "K21.9", "description": "Gastro-esophageal reflux disease without esophagitis"},
    {"code": "K21.0", "description": "Gastro-esophageal reflux disease with esophagitis"},
    {"code": "K29.70", "description": "Gastritis, unspecified, without bleeding"},
    {"code": "K29.00", "description": "Acute gastritis without bleeding"},
    {"code": "K30", "description": "Functional dyspepsia"},
    {"code": "K25.9", "description": "Gastric ulcer, unspecified as acute or chronic, without hemorrhage or perforation"},
    {"code": "K52.9", "description": "Noninfective gastroenteritis and colitis, unspecified"},
    {"code": "K58.9", "description": "Irritable bowel syndrome without diarrhea"},
    {"code": "K57.30", "description": "Diverticulosis of large intestine without perforation or abscess without bleeding"},
    {"code": "K59.00", "description": "Constipation, unspecified"},
    {"code": "K80.20", "description": "Calculus of gallbladder without cholecystitis without obstruction"},
    {"code": "K81.9", "description": "Cholecystitis, unspecified"},
    {"code": "K92.2", "description": "Gastrointestinal hemorrhage, unspecified"},
    {"code": "K62.5", "description": "Hemorrhage of anus and rectum"},
    {"code": "K64.9", "description": "Unspecified hemorrhoids"},
    {"code": "K51.90", "description": "Ulcerative colitis, unspecified, without complications"},
    {"code": "K50.90", "description": "Crohn's disease, unspecified, without complications"},
    {"code": "K76.0", "description": "Fatty (change of) liver, not elsewhere classified"},
    {"code": "K35.80", "description": "Unspecified acute appendicitis"},
    {"code": "K40.90", "description": "Unilateral inguinal hernia, without obstruction or gangrene, not specified as recurrent"},

    # --- Diseases of the skin and subcutaneous tissue (L00-L99) ---
    {"code": "L03.90", "description": "Cellulitis, unspecified"},
    {"code": "L03.115", "description": "Cellulitis of right lower limb"},
    {"code": "L03.116", "description": "Cellulitis of left lower limb"},
    {"code": "L02.91", "description": "Cutaneous abscess, unspecified"},
    {"code": "L08.9", "description": "Local infection of the skin and subcutaneous tissue, unspecified"},
    {"code": "L20.9", "description": "Atopic dermatitis, unspecified"},
    {"code": "L23.9", "description": "Allergic contact dermatitis, unspecified cause"},
    {"code": "L25.9", "description": "Unspecified contact dermatitis, unspecified cause"},
    {"code": "L30.9", "description": "Dermatitis, unspecified"},
    {"code": "L40.9", "description": "Psoriasis, unspecified"},
    {"code": "L50.9", "description": "Urticaria, unspecified"},
    {"code": "L70.0", "description": "Acne vulgaris"},
    {"code": "L71.9", "description": "Rosacea, unspecified"},
    {"code": "L98.9", "description": "Disorder of the skin and subcutaneous tissue, unspecified"},
    {"code": "L29.9", "description": "Pruritus, unspecified"},

    # --- Diseases of the musculoskeletal system and connective tissue (M00-M99) ---
    {"code": "M54.5", "description": "Low back pain"},
    {"code": "M54.2", "description": "Cervicalgia"},
    {"code": "M54.6", "description": "Pain in thoracic spine"},
    {"code": "M54.9", "description": "Dorsalgia, unspecified"},
    {"code": "M54.16", "description": "Radiculopathy, lumbar region"},
    {"code": "M54.12", "description": "Radiculopathy, cervical region"},
    {"code": "M51.26", "description": "Other intervertebral disc displacement, lumbar region"},
    {"code": "M47.816", "description": "Spondylosis without myelopathy or radiculopathy, lumbar region"},
    {"code": "M48.06", "description": "Spinal stenosis, lumbar region"},
    {"code": "M79.1", "description": "Myalgia"},
    {"code": "M79.7", "description": "Fibromyalgia"},
    {"code": "M79.601", "description": "Pain in right arm"},
    {"code": "M79.602", "description": "Pain in left arm"},
    {"code": "M25.561", "description": "Pain in right knee"},
    {"code": "M25.562", "description": "Pain in left knee"},
    {"code": "M25.511", "description": "Pain in right shoulder"},
    {"code": "M25.512", "description": "Pain in left shoulder"},
    {"code": "M25.551", "description": "Pain in right hip"},
    {"code": "M25.552", "description": "Pain in left hip"},
    {"code": "M17.9", "description": "Osteoarthritis of knee, unspecified"},
    {"code": "M16.9", "description": "Osteoarthritis of hip, unspecified"},
    {"code": "M19.90", "description": "Unspecified osteoarthritis, unspecified site"},
    {"code": "M06.9", "description": "Rheumatoid arthritis, unspecified"},
    {"code": "M10.9", "description": "Gout, unspecified"},
    {"code": "M65.9", "description": "Synovitis and tenosynovitis, unspecified"},
    {"code": "M81.0", "description": "Age-related osteoporosis without current pathological fracture"},
    {"code": "M72.2", "description": "Plantar fascial fibromatosis"},

    # --- Diseases of the genitourinary system (N00-N99) ---
    {"code": "N39.0", "description": "Urinary tract infection, site not specified"},
    {"code": "N18.9", "description": "Chronic kidney disease, unspecified"},
    {"code": "N18.6", "description": "End stage renal disease"},
    {"code": "N17.9", "description": "Acute kidney failure, unspecified"},
    {"code": "N19", "description": "Unspecified kidney failure"},
    {"code": "N20.0", "description": "Calculus of kidney"},
    {"code": "N20.1", "description": "Calculus of ureter"},
    {"code": "N30.00", "description": "Acute cystitis without hematuria"},
    {"code": "N30.90", "description": "Cystitis, unspecified without hematuria"},
    {"code": "N40.0", "description": "Benign prostatic hyperplasia without lower urinary tract symptoms"},
    {"code": "N40.1", "description": "Benign prostatic hyperplasia with lower urinary tract symptoms"},
    {"code": "N41.0", "description": "Acute prostatitis"},
    {"code": "N76.0", "description": "Acute vaginitis"},
    {"code": "N95.1", "description": "Menopausal and female climacteric states"},
    {"code": "N94.6", "description": "Dysmenorrhea, unspecified"},
    {"code": "N83.20", "description": "Unspecified ovarian cysts"},
    {"code": "N39.3", "description": "Stress incontinence (female) (male)"},
    {"code": "N39.41", "description": "Urge incontinence"},

    # --- Pregnancy, childbirth and the puerperium (O00-O9A) ---
    {"code": "O80", "description": "Encounter for full-term uncomplicated delivery"},
    {"code": "O21.0", "description": "Mild hyperemesis gravidarum"},

    # --- Symptoms, signs and abnormal clinical/lab findings (R00-R99) ---
    {"code": "R51.9", "description": "Headache, unspecified"},
    {"code": "R05.9", "description": "Cough, unspecified"},
    {"code": "R50.9", "description": "Fever, unspecified"},
    {"code": "R10.9", "description": "Unspecified abdominal pain"},
    {"code": "R10.11", "description": "Right upper quadrant pain"},
    {"code": "R10.31", "description": "Right lower quadrant pain"},
    {"code": "R10.32", "description": "Left lower quadrant pain"},
    {"code": "R10.84", "description": "Generalized abdominal pain"},
    {"code": "R07.9", "description": "Chest pain, unspecified"},
    {"code": "R06.02", "description": "Shortness of breath"},
    {"code": "R06.00", "description": "Dyspnea, unspecified"},
    {"code": "R11.2", "description": "Nausea with vomiting, unspecified"},
    {"code": "R11.0", "description": "Nausea"},
    {"code": "R11.10", "description": "Vomiting, unspecified"},
    {"code": "R19.7", "description": "Diarrhea, unspecified"},
    {"code": "R53.83", "description": "Other fatigue"},
    {"code": "R53.1", "description": "Weakness"},
    {"code": "R42", "description": "Dizziness and giddiness"},
    {"code": "R60.9", "description": "Edema, unspecified"},
    {"code": "R55", "description": "Syncope and collapse"},
    {"code": "R56.9", "description": "Unspecified convulsions"},
    {"code": "R00.2", "description": "Palpitations"},
    {"code": "R31.9", "description": "Hematuria, unspecified"},
    {"code": "R30.0", "description": "Dysuria"},
    {"code": "R73.03", "description": "Prediabetes"},
    {"code": "R73.09", "description": "Other abnormal glucose"},
    {"code": "R45.851", "description": "Suicidal ideations"},

    # --- Injury, poisoning and certain other external causes (S00-T88) ---
    {"code": "T78.40XA", "description": "Allergy, unspecified, initial encounter"},
    {"code": "T78.2XXA", "description": "Anaphylactic shock, unspecified, initial encounter"},
    {"code": "S52.501A", "description": "Unspecified fracture of the lower end of right radius, initial encounter for closed fracture"},
    {"code": "S72.001A", "description": "Fracture of unspecified part of neck of right femur, initial encounter for closed fracture"},
    {"code": "S06.0X0A", "description": "Concussion without loss of consciousness, initial encounter"},
    {"code": "S93.401A", "description": "Sprain of unspecified ligament of right ankle, initial encounter"},
    {"code": "S93.402A", "description": "Sprain of unspecified ligament of left ankle, initial encounter"},
    {"code": "S13.4XXA", "description": "Sprain of ligaments of cervical spine, initial encounter"},
    {"code": "S33.5XXA", "description": "Sprain of ligaments of lumbar spine, initial encounter"},
    {"code": "S01.01XA", "description": "Laceration without foreign body of scalp, initial encounter"},
    {"code": "T14.90XA", "description": "Injury, unspecified, initial encounter"},

    # --- Factors influencing health status and contact with health services (Z00-Z99) ---
    {"code": "Z00.00", "description": "Encounter for general adult medical examination without abnormal findings"},
    {"code": "Z00.01", "description": "Encounter for general adult medical examination with abnormal findings"},
    {"code": "Z23", "description": "Encounter for immunization"},
    {"code": "Z01.419", "description": "Encounter for gynecological examination (general) (routine) without abnormal findings"},
    {"code": "Z12.11", "description": "Encounter for screening for malignant neoplasm of colon"},
    {"code": "Z12.31", "description": "Encounter for screening mammogram for malignant neoplasm of breast"},
    {"code": "Z11.3", "description": "Encounter for screening for infections with a predominantly sexual mode of transmission"},
    {"code": "Z13.1", "description": "Encounter for screening for diabetes mellitus"},
    {"code": "Z79.4", "description": "Long term (current) use of insulin"},
    {"code": "Z79.01", "description": "Long term (current) use of anticoagulants"},
    {"code": "Z79.891", "description": "Long term (current) use of opiate analgesic"},
    {"code": "Z88.0", "description": "Allergy status to penicillin"},
    {"code": "Z87.891", "description": "Personal history of nicotine dependence"},
    {"code": "Z86.73", "description": "Personal history of transient ischemic attack (TIA), and cerebral infarction without residual deficits"},
    {"code": "Z51.11", "description": "Encounter for antineoplastic chemotherapy"},
    {"code": "Z71.3", "description": "Dietary counseling and surveillance"},
    {"code": "Z72.0", "description": "Tobacco use"},
    {"code": "Z96.641", "description": "Presence of right artificial hip joint"},
    {"code": "Z95.1", "description": "Presence of aortocoronary bypass graft"},
    {"code": "Z34.90", "description": "Encounter for supervision of normal pregnancy, unspecified, unspecified trimester"},
    {"code": "Z34.00", "description": "Encounter for supervision of normal first pregnancy, unspecified trimester"},
    {"code": "Z76.0", "description": "Encounter for issue of repeat prescription"},
    {"code": "Z09", "description": "Encounter for follow-up examination after completed treatment for conditions other than malignant neoplasm"},
    {"code": "Z47.1", "description": "Aftercare following joint replacement surgery"},
    {"code": "Z47.89", "description": "Encounter for other orthopedic aftercare"},
    {"code": "Z98.890", "description": "Other specified postprocedural states"},
]

# Pre-tokenize each description once at import time so search doesn't re-split
# 40 strings on every request. (In the pgvector version this is precomputed embeddings.)
_INDEX: list[tuple[dict, set[str]]] = [
    (row, set(row["description"].lower().replace(",", " ").replace("-", " ").split()))
    for row in ICD10_CATALOG
]

# Fast exact lookup by code, so we can validate AI-suggested codes against the
# catalog (uppercased key — codes are case-insensitive but conventionally upper).
_BY_CODE: dict[str, dict] = {row["code"].upper(): row for row in ICD10_CATALOG}


def validate_codes(codes: list[str]) -> list[dict]:
    """Filter a list of raw code strings down to the ones our catalog recognizes.

    The AI SUGGESTS codes; this is the guardrail that keeps a hallucinated or
    malformed code out of the record. We return the catalog's canonical
    `{code, description}` (not the model's text) and drop anything unknown,
    de-duplicating while preserving order.
    """
    seen: set[str] = set()
    valid: list[dict] = []
    for raw in codes:
        key = raw.strip().upper()
        row = _BY_CODE.get(key)
        if row is not None and key not in seen:
            seen.add(key)
            valid.append(row)
    return valid


def search(query: str, limit: int = 5) -> list[dict]:
    """Rank catalog codes by keyword overlap with the query.

    Scoring is deliberately simple (this is the fallback): +2 for each query token
    that appears as a whole word in the description, +1 for a substring match
    anywhere in the description. Codes with zero signal are dropped. A pgvector
    version would replace this with cosine distance over embeddings — same return
    shape, so callers don't change.
    """
    q = query.lower().strip()
    if not q:
        return []
    q_tokens = set(q.replace(",", " ").replace("-", " ").split())

    scored: list[tuple[int, dict]] = []
    for row, desc_tokens in _INDEX:
        score = 2 * len(q_tokens & desc_tokens)  # whole-word overlap
        if q in row["description"].lower():      # phrase substring bonus
            score += 1
        if score > 0:
            scored.append((score, row))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [row for _score, row in scored[:limit]]


@router.get("/search")
async def search_icd(
    q: str = Query(..., min_length=1, description="Free-text symptom or diagnosis"),
    limit: int = Query(5, ge=1, le=20),
    current_user: User = Depends(get_current_user),
) -> dict:
    """Suggest ICD-10 codes for a free-text query (authed).

    Returns `{"results": [{code, description}, ...]}`, highest-ranked first.
    """
    return {"results": search(q, limit)}


class ValidateRequest(BaseModel):
    """Body for POST /icd/validate — the raw codes the AI suggested."""

    codes: list[str]


@router.post("/validate")
async def validate_icd(
    body: ValidateRequest,
    current_user: User = Depends(get_current_user),
) -> dict:
    """Return only the AI-suggested codes that exist in our catalog (authed).

    Returns `{"results": [{code, description}, ...]}` with canonical descriptions,
    so the frontend can show trustworthy suggestions and never store a code we
    don't recognize.
    """
    return {"results": validate_codes(body.codes)}

import csv
import random
from pathlib import Path


YEARS = range(2023, 2028)
DISTRICTS = {
    'Talomo': ['Bago Aplaya', 'Baliok', 'Catalunan Grande', 'Talomo Proper'],
    'Buhangin': ['Acacia', 'Buhangin Proper', 'Cabantian', 'Sasa'],
    'Agdao': ['Agdao Proper', 'Leon Garcia', 'San Antonio'],
}
CLASSIFICATIONS = ['Confirmed', 'Suspected', 'Probable']
CLINICAL_CLASSIFICATIONS = [
    'Dengue without Warning Signs',
    'Dengue with Warning Signs',
    'Severe Dengue',
]


def get_case_count(year, week, random_generator):
    showcase_counts = {
        2023: {2: 10, 3: 10, 4: 10},
        2024: {1: 3, 2: 3, 3: 3, 4: 4, 20: 3},
        2025: {1: 1, 2: 1, 3: 1, 4: 1, 20: 2},
        2026: {**{week: 1 for week in range(1, 17)}, 17: 10, 18: 5, 19: 10, 20: 5},
    }
    if year in showcase_counts:
        return showcase_counts[year].get(week, 0)

    if year == 2027:
        if week <= 20:
            return 8
        if week <= 30:
            return 12
        if week <= 35:
            return 20 + (week - 30) * 4
        if week == 36:
            return 60
        if week == 37:
            return 40
        return 0

    seasonal_multiplier = 2.8 if 24 <= week <= 36 else 1.0
    return max(0, int(random_generator.gauss(7 * seasonal_multiplier, 2.5)))


def generate_rows(seed=2026):
    random_generator = random.Random(seed)
    rows = []
    case_number = 1
    for year in YEARS:
        for week in range(1, 53):
            case_count = get_case_count(year, week, random_generator)
            for _ in range(case_count):
                district = random_generator.choice(list(DISTRICTS))
                barangay = random_generator.choice(DISTRICTS[district])
                rows.append({
                    'Morbidity Year': year,
                    'Morbidity Month': min(12, ((week - 1) // 4) + 1),
                    'Morbidity Week': week,
                    'Clinical Classification': random_generator.choices(
                        CLINICAL_CLASSIFICATIONS, weights=[0.62, 0.28, 0.10]
                    )[0],
                    'Case Classification': random_generator.choices(
                        CLASSIFICATIONS, weights=[0.58, 0.30, 0.12]
                    )[0],
                    'District': '' if random_generator.random() < 0.03 else district,
                    'Barangay': '' if random_generator.random() < 0.03 else barangay,
                    'AgeYears': '' if random_generator.random() < 0.04 else random_generator.randint(1, 85),
                })
                rows[-1]['Case ID'] = f'DENGUE-{year}-{case_number:05d}'
                case_number += 1
    return rows


def main():
    output_path = Path(__file__).with_name('dengue_test_data_2023_2027.csv')
    fieldnames = [
        'Case ID', 'Morbidity Year', 'Morbidity Month', 'Morbidity Week',
        'Clinical Classification', 'Case Classification', 'District', 'Barangay', 'AgeYears',
    ]
    with output_path.open('w', newline='', encoding='utf-8') as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(generate_rows())
    print(f'Generated {output_path}')


if __name__ == '__main__':
    main()
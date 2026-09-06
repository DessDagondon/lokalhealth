import csv
import random
from pathlib import Path


YEARS = range(2023, 2027)
DISTRICTS = {
    'Talomo': ['Bago Aplaya', 'Baliok', 'Catalunan Grande', 'Talomo Proper'],
    'Buhangin': ['Acacia', 'Buhangin Proper', 'Cabantian', 'Sasa'],
    'Agdao': ['Agdao Proper', 'Leon Garcia', 'San Antonio'],
}
CLASSIFICATIONS = ['Confirmed', 'Suspected', 'Probable']


def generate_rows(seed=2026):
    random_generator = random.Random(seed)
    rows = []
    case_number = 1
    for year in YEARS:
        for week in range(1, 53):
            seasonal_multiplier = 2.8 if 24 <= week <= 36 else 1.0
            case_count = max(0, int(random_generator.gauss(7 * seasonal_multiplier, 2.5)))
            for _ in range(case_count):
                district = random_generator.choice(list(DISTRICTS))
                barangay = random_generator.choice(DISTRICTS[district])
                rows.append({
                    'Morbidity Year': year,
                    'Morbidity Month': min(12, ((week - 1) // 4) + 1),
                    'Morbidity Week': week,
                    'Clinical Classification': random_generator.choices(
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
    output_path = Path(__file__).with_name('dengue_test_data_2023_2026.csv')
    fieldnames = [
        'Case ID', 'Morbidity Year', 'Morbidity Month', 'Morbidity Week',
        'Clinical Classification', 'District', 'Barangay', 'AgeYears',
    ]
    with output_path.open('w', newline='', encoding='utf-8') as output_file:
        writer = csv.DictWriter(output_file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(generate_rows())
    print(f'Generated {output_path}')


if __name__ == '__main__':
    main()
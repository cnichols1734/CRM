/**
 * Texas Owner's Title Policy Basic Premium Calculator
 * Effective Date: September 1, 2019
 * 
 * This module calculates the owner's title insurance premium based on the
 * Texas Department of Insurance approved rate schedule.
 * 
 * Usage:
 *   const premium = calculateTexasTitlePremium(salesPrice);
 */

(function(global) {
    'use strict';

    // Rate table for $25,000 to $100,000 (in $500 increments)
    // Key: ceiling amount, Value: premium in whole dollars
    const RATE_TABLE = {
        25000: 328, 25500: 331, 26000: 335, 26500: 338, 27000: 340,
        27500: 343, 28000: 347, 28500: 350, 29000: 355, 29500: 358,
        30000: 361, 30500: 364, 31000: 368, 31500: 371, 32000: 374,
        32500: 378, 33000: 381, 33500: 385, 34000: 388, 34500: 392,
        35000: 395, 35500: 398, 36000: 401, 36500: 405, 37000: 408,
        37500: 412, 38000: 416, 38500: 419, 39000: 421, 39500: 425,
        40000: 428, 40500: 433, 41000: 435, 41500: 439, 42000: 442,
        42500: 446, 43000: 448, 43500: 452, 44000: 456, 44500: 459,
        45000: 463, 45500: 466, 46000: 469, 46500: 473, 47000: 475,
        47500: 478, 48000: 483, 48500: 487, 49000: 490, 49500: 493,
        50000: 496, 50500: 499, 51000: 501, 51500: 505, 52000: 510,
        52500: 514, 53000: 516, 53500: 520, 54000: 523, 54500: 526,
        55000: 529, 55500: 532, 56000: 537, 56500: 540, 57000: 543,
        57500: 547, 58000: 551, 58500: 553, 59000: 556, 59500: 560,
        60000: 564, 60500: 568, 61000: 571, 61500: 573, 62000: 577,
        62500: 581, 63000: 583, 63500: 587, 64000: 591, 64500: 594,
        65000: 597, 65500: 600, 66000: 604, 66500: 609, 67000: 612,
        67500: 613, 68000: 617, 68500: 621, 69000: 624, 69500: 627,
        70000: 631, 70500: 635, 71000: 639, 71500: 641, 72000: 644,
        72500: 648, 73000: 651, 73500: 654, 74000: 658, 74500: 662,
        75000: 666, 75500: 668, 76000: 671, 76500: 674, 77000: 678,
        77500: 681, 78000: 685, 78500: 689, 79000: 693, 79500: 694,
        80000: 698, 80500: 702, 81000: 706, 81500: 708, 82000: 711,
        82500: 716, 83000: 720, 83500: 722, 84000: 725, 84500: 729,
        85000: 732, 85500: 735, 86000: 738, 86500: 743, 87000: 747,
        87500: 749, 88000: 752, 88500: 756, 89000: 760, 89500: 762,
        90000: 765, 90500: 769, 91000: 773, 91500: 777, 92000: 779,
        92500: 783, 93000: 786, 93500: 790, 94000: 791, 94500: 796,
        95000: 801, 95500: 804, 96000: 805, 96500: 809, 97000: 813,
        97500: 817, 98000: 820, 98500: 824, 99000: 827, 99500: 830,
        100000: 832
    };

    // Tier definitions for amounts > $100,000
    const TIERS = [
        { max: 1000000,    base: 832,    rate: 0.00527, floor: 100000 },    // Tier A
        { max: 5000000,    base: 5575,   rate: 0.00433, floor: 1000000 },   // Tier B
        { max: 15000000,   base: 22895,  rate: 0.00357, floor: 5000000 },   // Tier C
        { max: 25000000,   base: 58595,  rate: 0.00254, floor: 15000000 },  // Tier D
        { max: 50000000,   base: 83995,  rate: 0.00152, floor: 25000000 },  // Tier E
        { max: 100000000,  base: 121995, rate: 0.00138, floor: 50000000 },  // Tier F
        { max: Infinity,   base: 190995, rate: 0.00124, floor: 100000000 }  // Tier G
    ];

    const MIN_PREMIUM = 328;
    const MIN_POLICY_AMOUNT = 25000;

    /**
     * Round up to the next $500 increment
     * @param {number} amount - The policy amount
     * @returns {number} - The ceiling rounded up to nearest $500
     */
    function roundUpToNext500(amount) {
        return Math.ceil(amount / 500) * 500;
    }

    /**
     * Round to nearest whole dollar
     * @param {number} value - The value to round
     * @returns {number} - Rounded whole dollar amount
     */
    function roundToNearestDollar(value) {
        return Math.round(value);
    }

    /**
     * Calculate the Texas Owner's Title Policy Basic Premium
     * Based on Texas DOI approved rates effective September 1, 2019
     * 
     * @param {number} salesPrice - The sales price / policy amount
     * @returns {number} - The premium in whole dollars (0 if invalid input)
     */
    function calculateTexasTitlePremium(salesPrice) {
        // Handle invalid input
        if (!salesPrice || isNaN(salesPrice) || salesPrice <= 0) {
            return 0;
        }

        const amount = parseFloat(salesPrice);

        // 1) Minimum premium (amount <= $25,000)
        if (amount <= MIN_POLICY_AMOUNT) {
            return MIN_PREMIUM;
        }

        // 2) Table lookup ($25,001 - $100,000)
        if (amount <= 100000) {
            const ceiling = roundUpToNext500(amount);
            // Clamp to table range
            const lookupKey = Math.min(ceiling, 100000);
            return RATE_TABLE[lookupKey] || MIN_PREMIUM;
        }

        // 3) Formula tiers (> $100,000)
        for (const tier of TIERS) {
            if (amount <= tier.max) {
                const excess = amount - tier.floor;
                const premium = tier.base + roundToNearestDollar(excess * tier.rate);
                return premium;
            }
        }

        // Fallback (should never reach here)
        return 0;
    }

    // Expose to global scope
    global.calculateTexasTitlePremium = calculateTexasTitlePremium;

})(typeof window !== 'undefined' ? window : this);

